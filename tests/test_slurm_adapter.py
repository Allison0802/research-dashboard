import importlib
import subprocess

import pytest

from research_dashboard.db import init_db
from research_dashboard.executions import list_executions, register_execution
from research_dashboard.settings import Settings


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    try:
        yield database
    finally:
        database.close()


def slurm():
    return importlib.import_module("research_dashboard.adapters.slurm")


def register_slurm_execution(
    connection, *, execution_id="execution-1", job_id="12345"
):
    return register_execution(
        connection,
        {
            "execution_id": execution_id,
            "backend": "slurm",
            "external_id": job_id,
            "current_state": "SUBMITTED",
        },
    )


@pytest.mark.parametrize(
    "raw_state, expected",
    [
        ("SUBMITTED", "SUBMITTED"),
        ("NEW", "SUBMITTED"),
        ("PD", "QUEUED"),
        ("PENDING", "QUEUED"),
        ("CF", "QUEUED"),
        ("CONFIGURING", "QUEUED"),
        ("REQUEUED", "QUEUED"),
        ("RESV_DEL_HOLD", "QUEUED"),
        ("R", "RUNNING"),
        ("RUNNING", "RUNNING"),
        ("S", "RUNNING"),
        ("SUSPENDED", "RUNNING"),
        ("CG", "RUNNING"),
        ("COMPLETING", "RUNNING"),
        ("CD", "COMPLETED"),
        ("COMPLETED", "COMPLETED"),
        ("F", "FAILED"),
        ("FAILED", "FAILED"),
        ("BOOT_FAIL", "FAILED"),
        ("DEADLINE", "FAILED"),
        ("NODE_FAIL", "FAILED"),
        ("OUT_OF_MEMORY", "FAILED"),
        ("PREEMPTED", "FAILED"),
        ("TIMEOUT", "FAILED"),
        ("SPECIAL_EXIT", "FAILED"),
        ("CA", "CANCELLED"),
        ("CANCELLED", "CANCELLED"),
        ("NOT_A_SLURM_STATE", "UNKNOWN"),
    ],
)
def test_parse_slurm_record_preserves_complete_generic_state_normalization(
    raw_state, expected
):
    parsed = slurm().parse_slurm_record(
        f"12345|{raw_state}+ trailing detail|00:00:01|00:10:00|None",
        source="squeue",
    )

    assert parsed["state"] == expected
    assert parsed["raw_record"]["raw_scheduler_state"] == (
        f"{raw_state}+ trailing detail"
    )


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        " 12345",
        "12345 ",
        "12345 12346",
        "12345;id",
        "12345$(id)",
        ["12345"],
    ],
)
def test_validate_slurm_job_id_rejects_anything_but_one_numeric_token(job_id):
    with pytest.raises(ValueError, match="numeric Slurm job ID"):
        slurm().validate_slurm_job_id(job_id)


@pytest.mark.parametrize(
    "ssh_target", ["login.example.org", "user@login.example.org"]
)
def test_validate_ssh_target_accepts_one_safe_host_token(ssh_target):
    assert slurm().validate_ssh_target(ssh_target) == ssh_target


@pytest.mark.parametrize(
    "ssh_target",
    [
        "",
        " user@login.example.org",
        "user@login.example.org ",
        "user@login.example.org other",
        "user@login.example.org;id",
        "user@login.example.org$(id)",
        "-oProxyCommand=bad",
        ["user@login.example.org"],
    ],
)
def test_validate_ssh_target_rejects_extra_tokens_and_shell_punctuation(ssh_target):
    with pytest.raises(ValueError, match="safe user@host or host token"):
        slurm().validate_ssh_target(ssh_target)


def test_poll_slurm_execution_records_squeue_observation(connection):
    register_slurm_execution(connection)
    commands = []

    def runner(target, command):
        commands.append((target, command))
        return "12345|RUNNING|00:00:01|00:10:00|None"

    result = slurm().poll_slurm_execution(
        connection,
        execution_id="execution-1",
        ssh_target="user@login.example.org",
        runner=runner,
    )

    assert commands == [
        (
            "user@login.example.org",
            "squeue -h --jobs=12345 -o '%i|%T|%M|%L|%R'",
        )
    ]
    assert result["observation"]["state"] == "RUNNING"
    assert result["record"]["raw_record"]["source"] == "squeue"
    assert list_executions(connection)[0]["current_state"] == "RUNNING"


def test_poll_slurm_execution_falls_back_from_empty_queue_to_accounting(connection):
    register_slurm_execution(connection)
    commands = []

    def runner(_target, command):
        commands.append(command)
        if command.startswith("squeue"):
            return ""
        return "12345|COMPLETED|0:0|00:04:12|1024K"

    result = slurm().poll_slurm_execution(
        connection,
        execution_id="execution-1",
        ssh_target="login.example.org",
        runner=runner,
    )

    assert commands == [
        "squeue -h --jobs=12345 -o '%i|%T|%M|%L|%R'",
        "sacct -X -n --jobs=12345 --format=JobIDRaw,State,ExitCode,Elapsed,MaxRSS -P",
    ]
    assert result["observation"]["state"] == "COMPLETED"
    assert result["record"]["raw_record"]["source"] == "sacct"


def test_poll_slurm_execution_falls_back_for_generic_invalid_job_diagnostic(connection):
    register_slurm_execution(connection)

    def runner(_target, command):
        if command.startswith("squeue"):
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="slurm_load_jobs error: Invalid job id specified",
            )
        return "12345|COMPLETED|0:0|00:04:12|1024K"

    result = slurm().poll_slurm_execution(
        connection,
        execution_id="execution-1",
        ssh_target="login.example.org",
        runner=runner,
    )

    assert result["observation"]["state"] == "COMPLETED"


def test_poll_slurm_execution_rejects_nonzero_scheduler_errors(connection):
    register_slurm_execution(connection)

    def runner(_target, command):
        raise subprocess.CalledProcessError(1, command, stderr="permission denied")

    with pytest.raises(slurm().SlurmAdapterError, match="squeue query failed"):
        slurm().poll_slurm_execution(
            connection,
            execution_id="execution-1",
            ssh_target="login.example.org",
            runner=runner,
        )


def test_poll_slurm_execution_requires_a_registered_slurm_backend(connection):
    register_execution(
        connection,
        {
            "execution_id": "execution-local",
            "backend": "local",
            "external_id": "run-1",
            "current_state": "SUBMITTED",
        },
    )

    with pytest.raises(slurm().SlurmAdapterError, match="backend 'slurm'"):
        slurm().poll_slurm_execution(
            connection,
            execution_id="execution-local",
            ssh_target="login.example.org",
            runner=lambda _target, _command: pytest.fail("runner must not run"),
        )


def test_poll_slurm_execution_rejects_a_non_numeric_stored_external_id(connection):
    register_slurm_execution(connection, job_id="not-a-number")

    with pytest.raises(ValueError, match="numeric Slurm job ID"):
        slurm().poll_slurm_execution(
            connection,
            execution_id="execution-1",
            ssh_target="login.example.org",
            runner=lambda _target, _command: pytest.fail("runner must not run"),
        )
