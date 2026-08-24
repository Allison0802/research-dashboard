"""Optional Slurm execution adapter with bounded scheduler polling."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import re
import sqlite3
import subprocess
from typing import Any, Literal
from uuid import uuid4

from ..executions import record_execution_observation
from ..registry import _validate_connection


_SLURM_JOB_ID_PATTERN = re.compile(r"[0-9]+")
_SSH_TARGET_PATTERN = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*"
)
_SLURM_STATES = {
    "SUBMITTED": "SUBMITTED",
    "NEW": "SUBMITTED",
    "PD": "QUEUED",
    "PENDING": "QUEUED",
    "CF": "QUEUED",
    "CONFIGURING": "QUEUED",
    "REQUEUED": "QUEUED",
    "RESV_DEL_HOLD": "QUEUED",
    "RUNNING": "RUNNING",
    "R": "RUNNING",
    "S": "RUNNING",
    "SUSPENDED": "RUNNING",
    "CG": "RUNNING",
    "COMPLETING": "RUNNING",
    "CD": "COMPLETED",
    "COMPLETED": "COMPLETED",
    "F": "FAILED",
    "FAILED": "FAILED",
    "BOOT_FAIL": "FAILED",
    "DEADLINE": "FAILED",
    "NODE_FAIL": "FAILED",
    "OUT_OF_MEMORY": "FAILED",
    "PREEMPTED": "FAILED",
    "TIMEOUT": "FAILED",
    "SPECIAL_EXIT": "FAILED",
    "CANCELLED": "CANCELLED",
    "CA": "CANCELLED",
}
_INVALID_JOB_DIAGNOSTICS = frozenset(
    {
        "invalid job id specified",
        "squeue: error: invalid job id specified",
        "slurm_load_jobs error: invalid job id specified",
    }
)

Runner = Callable[[str, str], str]


class SlurmAdapterError(RuntimeError):
    """A Slurm adapter operation did not produce usable scheduler evidence."""


def validate_slurm_job_id(value: str) -> str:
    """Validate one exact, numeric Slurm job ID."""
    if not isinstance(value, str) or _SLURM_JOB_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("job_id must be exactly one numeric Slurm job ID")
    return value


def validate_ssh_target(value: str) -> str:
    """Validate one safe ``host`` or ``user@host`` SSH destination token."""
    if not isinstance(value, str) or _SSH_TARGET_PATTERN.fullmatch(value) is None:
        raise ValueError("ssh_target must be a safe user@host or host token")
    return value


def _normalize_state(raw_state: str) -> str:
    cleaned = raw_state.strip().upper().split(" ", 1)[0].rstrip("+")
    return _SLURM_STATES.get(cleaned, "UNKNOWN")


def parse_slurm_record(
    record: str | Mapping[str, Any], *, source: Literal["squeue", "sacct"]
) -> dict[str, Any]:
    """Parse one bounded Slurm scheduler record into generic execution state."""
    if source not in {"squeue", "sacct"}:
        raise ValueError(f"unsupported Slurm record source {source!r}")
    if isinstance(record, Mapping):
        values = {str(key): value for key, value in record.items()}
        job_id = str(values.get("job_id", values.get("JobIDRaw", ""))).strip()
        raw_state = str(values.get("state", values.get("State", ""))).strip()
        exit_code = values.get("exit_code", values.get("ExitCode"))
        elapsed = values.get("elapsed", values.get("Elapsed"))
        max_memory = values.get("max_memory", values.get("MaxRSS"))
        reason = values.get("reason", values.get("Reason"))
        raw_line = values.get("raw_line")
    else:
        raw_line = record.strip()
        fields = [field.strip() for field in raw_line.split("|")]
        if len(fields) < 2 or fields[0] in {"JobIDRaw", "JobID"}:
            raise ValueError("Slurm record must contain a data row")
        job_id, raw_state = fields[:2]
        if source == "sacct":
            exit_code = fields[2] if len(fields) > 2 else None
            elapsed = fields[3] if len(fields) > 3 else None
            max_memory = fields[4] if len(fields) > 4 else None
            reason = None
        else:
            exit_code = None
            elapsed = fields[2] if len(fields) > 2 else None
            max_memory = None
            reason = fields[4] if len(fields) > 4 else None

    if not job_id or not raw_state:
        raise ValueError("Slurm record must contain job ID and state")
    raw_record = {
        "source": source,
        "job_id": job_id,
        "raw_scheduler_state": raw_state,
        "exit_code": exit_code,
        "elapsed": elapsed,
        "max_memory": max_memory,
        "reason": reason,
        "raw_line": raw_line,
    }
    return {
        "job_id": job_id,
        "state": _normalize_state(raw_state),
        "exit_code": exit_code,
        "elapsed": elapsed,
        "max_memory": max_memory,
        "raw_record": raw_record,
    }


def _parse_matching_record(
    output: str, *, source: Literal["squeue", "sacct"], job_id: str
) -> dict[str, Any] | None:
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            records.append(parse_slurm_record(line, source=source))
        except ValueError as error:
            raise SlurmAdapterError(
                f"Slurm {source} response for execution job {job_id!r} "
                "contained an invalid record"
            ) from error
    if not records:
        return None
    if any(record["job_id"] != job_id for record in records):
        raise SlurmAdapterError(
            f"Slurm {source} response for execution job {job_id!r} "
            "was ambiguous and contained unrelated records"
        )
    if len(records) != 1:
        raise SlurmAdapterError(
            f"Slurm {source} response for execution job {job_id!r} was ambiguous"
        )
    return records[0]


def _text_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)


def _is_invalid_job_diagnostic(*outputs: str | bytes | None) -> bool:
    lines = [
        line.casefold().strip()
        for output in outputs
        for line in _text_output(output).splitlines()
        if line.strip()
    ]
    return bool(lines) and all(line in _INVALID_JOB_DIAGNOSTICS for line in lines)


def _error_detail(error: subprocess.CalledProcessError) -> str:
    diagnostics = [
        _text_output(output)
        for output in (error.stdout, error.stderr)
        if output
    ]
    return ": " + "\n".join(diagnostics) if diagnostics else ""


def _slurm_query(runner: Runner, ssh_target: str, job_id: str) -> dict[str, Any]:
    queue_command = f"squeue -h --jobs={job_id} -o '%i|%T|%M|%L|%R'"
    try:
        queue_output = runner(ssh_target, queue_command)
    except subprocess.CalledProcessError as error:
        if not _is_invalid_job_diagnostic(error.stdout, error.stderr):
            raise SlurmAdapterError(
                f"Slurm squeue query failed for execution job {job_id!r}"
                + _error_detail(error)
            ) from error
        queue_output = ""
    if _is_invalid_job_diagnostic(queue_output):
        queue_output = ""
    record = _parse_matching_record(queue_output, source="squeue", job_id=job_id)
    if record is not None:
        return record

    accounting_command = (
        f"sacct -X -n --jobs={job_id} "
        "--format=JobIDRaw,State,ExitCode,Elapsed,MaxRSS -P"
    )
    try:
        accounting_output = runner(ssh_target, accounting_command)
    except subprocess.CalledProcessError as error:
        raise SlurmAdapterError(
            f"Slurm sacct query failed for execution job {job_id!r}"
            + _error_detail(error)
        ) from error
    record = _parse_matching_record(accounting_output, source="sacct", job_id=job_id)
    if record is None:
        raise SlurmAdapterError(
            f"Slurm returned no record for execution job {job_id!r}"
        )
    return record


def ssh_runner(ssh_target: str, command: str) -> str:
    """Run one bounded Slurm query through non-interactive SSH."""
    target = validate_ssh_target(ssh_target)
    completed = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "--",
            target,
            command,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.stdout


def poll_slurm_execution(
    connection,
    *,
    execution_id: str,
    ssh_target: str,
    runner: Runner = ssh_runner,
) -> dict[str, Any]:
    """Poll one registered Slurm execution and append its normalized observation."""
    _validate_connection(connection)
    target = validate_ssh_target(ssh_target)
    row = connection.execute(
        "SELECT execution_id, backend, external_id FROM executions WHERE execution_id = ?",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise SlurmAdapterError(f"execution {execution_id!r} is not registered")
    execution = dict(row)
    if execution["backend"] != "slurm":
        raise SlurmAdapterError(
            f"execution {execution_id!r} must use backend 'slurm'"
        )
    job_id = validate_slurm_job_id(execution["external_id"])
    record = _slurm_query(runner, target, job_id)
    observation = record_execution_observation(
        connection,
        {
            "observation_id": str(uuid4()),
            "execution_id": execution_id,
            "state": record["state"],
            "observed_at": datetime.now(timezone.utc),
            "raw_record": record["raw_record"],
        },
    )
    return {"execution": execution, "observation": observation, "record": record}
