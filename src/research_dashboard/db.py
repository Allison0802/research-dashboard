from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator

from .settings import Settings, load_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SAVEPOINT_IDS = count()
REQUIRED_SCHEMA_TABLES = frozenset(
    {
        "projects",
        "project_roots",
        "governing_plans",
        "events",
        "event_evidence",
        "state_conflicts",
        "risks",
        "risk_transitions",
        "review_state",
        "named_checkpoints",
        "executions",
        "execution_observations",
        "roadmap_items",
        "todos",
        "roadmap_sync_state",
        "roadmap_proposal_batches",
    }
)


def connect_db(
    settings: Settings | None = None,
    *,
    read_only: bool = False,
) -> sqlite3.Connection:
    settings = settings or load_settings()
    if read_only:
        if not settings.database_path.is_file():
            raise FileNotFoundError(
                "dashboard database is not initialized at "
                f"{settings.database_path}; run `research-dashboard init` first"
            )
        try:
            database_uri = f"{settings.database_path.absolute().as_uri()}?mode=ro"
            connection = sqlite3.connect(database_uri, uri=True)
        except sqlite3.Error as error:
            raise RuntimeError(
                f"cannot open dashboard database for read-only validation: {error}"
            ) from error
    else:
        settings.runtime_root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(settings.database_path)

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if read_only:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        missing_tables = sorted(
            REQUIRED_SCHEMA_TABLES - {row[0] for row in rows}
        )
        if missing_tables:
            connection.close()
            raise RuntimeError(
                "dashboard database is not initialized; missing tables: "
                + ", ".join(missing_tables)
                + "; run `research-dashboard init` first"
            )
    return connection


def init_db(settings: Settings | None = None) -> sqlite3.Connection:
    connection = connect_db(settings)
    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.commit()
    except Exception:
        connection.close()
        raise
    return connection


def create_snapshot(
    destination: Path | str,
    settings: Settings | None = None,
) -> Path:
    """Create a timestamped, consistent SQLite snapshot of the live database."""
    settings = settings or load_settings()
    destination = Path(destination).expanduser()
    if not destination.exists():
        raise FileNotFoundError(
            f"snapshot destination does not exist: {destination}"
        )
    if not destination.is_dir():
        raise NotADirectoryError(
            f"snapshot destination is not a directory: {destination}"
        )

    snapshot_path = destination / (
        "research-dashboard-"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite3"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot_path.name}.",
        suffix=".tmp",
        dir=destination,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    source_connection = None
    target_connection = None
    try:
        try:
            source_connection = connect_db(settings, read_only=True)
            target_connection = sqlite3.connect(temporary_path)
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            if target_connection is not None:
                target_connection.close()
            if source_connection is not None:
                source_connection.close()

        try:
            os.link(temporary_path, snapshot_path)
        except FileExistsError as error:
            raise FileExistsError(
                f"refusing to overwrite existing snapshot: {snapshot_path}"
            ) from error
    finally:
        temporary_path.unlink(missing_ok=True)
    return snapshot_path


@contextmanager
def transaction(
    connection: sqlite3.Connection,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    outermost = not connection.in_transaction
    if outermost:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            try:
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return

    savepoint = f"dashboard_transaction_{next(_SAVEPOINT_IDS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield connection
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
