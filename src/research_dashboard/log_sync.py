"""Import dated entries from logs at explicitly admitted project roots."""

from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from .events import ingest_event
from .registry import is_admitted_project, list_projects, list_project_roots


LOG_NAMES = ("project_reports/PROJECT_LOG.md", "PROJECT_LOG.md", "CHANGES.md", "CHANGELOG.md")
ENTRY = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*[—–:-]?\s*(.*)$", re.MULTILINE)


def latest_log_entry(text: str) -> dict | None:
    entries = list(ENTRY.finditer(text))
    valid = []
    for index, match in enumerate(entries):
        try:
            day = date.fromisoformat(match[1])
        except ValueError:
            continue
        end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
        valid.append((day, match[2], text[match.end():end].strip()))
    if not valid:
        return None
    latest = max(item[0] for item in valid)
    daily = [(title, body) for day, title, body in valid if day == latest]
    title = (daily[0][0] or "Project log update") if len(daily) == 1 else f"{len(daily)} project updates recorded"
    body = "\n\n".join(f"### {heading}\n{content}" for heading, content in daily)

    def section(pattern: str) -> str | None:
        if len(daily) != 1:
            return None
        match = re.search(rf"^### (?:{pattern})\s*\n(.*?)(?=^### |\Z)", body, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        return " ".join(line.strip() for line in match[1].splitlines() if line.strip()) or None if match else None

    return {
        "date": latest.isoformat(), "title": title, "body": body,
        "current_step": section("Current step"), "purpose": section("Purpose"),
        "next_step": section("Remaining work|Next steps?|Next bounded work"),
    }


def sync_project_logs(connection, *, dry_run: bool = False) -> list[dict]:
    receipts = []
    for project in list_projects(connection):
        if not is_admitted_project(project):
            continue
        for root in list_project_roots(connection, project["project_id"]):
            for name in LOG_NAMES:
                path = Path(root) / name
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    with path.open(encoding="utf-8") as source:
                        text = source.read(200_001)
                    if len(text) > 200_000:
                        continue
                    entry = latest_log_entry(text)
                except (OSError, UnicodeError):
                    continue
                if entry is None:
                    continue
                digest = hashlib.sha256(re.sub(r"\s+", " ", entry["date"] + "\n" + entry["body"]).encode()).hexdigest()
                event_id = str(uuid5(NAMESPACE_URL, f"dashboard-project-log:{project['project_id']}:{path}:{digest}"))
                if connection.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone():
                    continue
                receipt = {"project_id": project["project_id"], "path": str(path), "event_id": event_id, "status": "would_import" if dry_run else "imported"}
                receipts.append(receipt)
                if dry_run:
                    continue
                observed = datetime.combine(date.fromisoformat(entry["date"]), datetime.min.time(), tzinfo=timezone.utc)
                ingest_event(connection, {
                    "event_id": event_id, "project_id": project["project_id"],
                    "task_key": "project-log:" + name,
                    "event_type": "project_log_update", "importance": "Routine change",
                    "epistemic_status": "Observed", "what_changed": entry["title"],
                    "context": f"Project log dated {entry['date']} records the following. The source provides a date only; midnight UTC is an ordering convention, not an observed time. Same-day entries retain file order without assuming recency. Importing this log does not independently validate analytical claims.\n\n{entry['body']}",
                    "progress": {
                        "current_step": entry["current_step"] or entry["title"],
                        "purpose": entry["purpose"] or "Record dated project-log activity",
                        "next_step": entry["next_step"],
                    },
                    "next_action": entry["next_step"],
                    "source_session": "project-log:" + str(path), "observed_at": observed,
                    "evidence": [{"evidence_type": "project_log", "locator": str(path), "authority": 1}],
                })
    return receipts
