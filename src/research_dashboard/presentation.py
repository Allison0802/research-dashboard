"""Display-only formatting; original research records remain untouched."""

import re
from datetime import datetime, timezone
from .registry import is_admitted_project


def project_updates(projects: list[dict], *, include_archived: bool = False) -> list[dict]:
    """One latest observation and confirmed plan step per visible project."""
    def order(event):
        return (datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00")), event["sequence"])

    rows = []
    for summary in projects:
        if not is_admitted_project(summary["project"]) and not include_archived:
            continue
        update = max(summary["timeline"], key=order, default=None)
        category = "regular"
        if update:
            if update["importance"] == "Decision needed":
                category = "needs-me"
            elif update["importance"] == "Critical blocker" or update.get("new_state") == "Blocked":
                category = "blocked"
        progress = (
            update.get("progress")
            if update and update.get("evidence_availability") == "available"
            else None
        )
        plan = summary.get("governing_plan")
        sync = summary.get("plan_sync") or {}
        event_plan = update.get("governing_plan_path") if update else None
        if progress and plan and event_plan and (
            event_plan != plan["path"] or sync.get("status") != "current"
            or sync.get("plan_path") != plan["path"]
        ):
            progress_kind, progress_binding = "historical", "This recorded step does not match a current registered plan version."
        elif progress and plan and event_plan:
            progress_kind, progress_binding = "latest", "Event plan version unconfirmed."
        elif progress:
            progress_kind = "latest"
            progress_binding = "Plan binding unconfirmed." if event_plan or plan else None
        else:
            progress_kind, progress_binding = None, None
        rows.append({
            "project": summary["project"], "update": update, "category": category,
            "progress_brief": progress,
            "progress_kind": progress_kind,
            "progress_binding": progress_binding,
            "plan_step": _plan_step(summary),
            "activity": homepage_text(update.get("what_changed")) if update else None,
            "next_action": (
                homepage_text(update["next_action"])
                if update and update.get("next_action") and update.get("evidence_availability", "available") == "available"
                else None
            ),
        })
    return sorted(rows, key=lambda row: order(row["update"]) if row["update"] else
                  (datetime.min.replace(tzinfo=timezone.utc), -1), reverse=True)


def readable_text(value: str | None) -> str:
    """Hide explicit scheduler identifiers and long fingerprints, never results."""
    text = value or ""
    text = re.sub(r"\b(job|array)\s+(?:ID\s*[:=]?\s*)?\d{6,}(?:_[\d,\[\]-]+)?\b",
                  r"\1", text, flags=re.IGNORECASE)
    return re.sub(r"\b[a-fA-F0-9]{32,64}\b", "[fingerprint]", text)


def homepage_text(value: str | None) -> str:
    """Keep technical task identifiers in details, out of the project brief."""
    text = readable_text(value)
    return re.sub(
        r"\b[Tt]asks?\s+#?\d+(?:[/_-]\d+)?(?:\s*,?\s*(?:and\s+)?#?\d+(?:[/_-]\d+)?)*",
        "tasks", text,
    )


def _plan_step(summary: dict) -> dict | None:
    plan = summary.get("governing_plan")
    sync = summary.get("plan_sync") or {}
    if not plan or sync.get("status") != "current" or sync.get("plan_path") != plan["path"]:
        return None
    items = sorted(
        (item for item in summary.get("roadmap", [])
         if item.get("source_plan_path") == plan["path"]
         and item.get("source_key") and item.get("parent_item_id") is None),
        key=lambda item: (item["position"], item["roadmap_item_id"]),
    )
    active = [item for item in items if item["status"] in {"In progress", "Waiting", "Blocked"}]
    if len(active) > 1:
        return None
    current = active[0] if active else next(
        (item for item in items if item["status"] == "Not started"), None
    )
    if current is None:
        return None
    later = items[items.index(current) + 1:]
    following = next((item for item in later if item["status"] not in {"Done", "Skipped"}), None)
    return {
        "title": readable_text(current["title"]),
        "note": homepage_text(current["note"]) if current.get("note") else None,
        "status": current["status"],
        "next": readable_text(following["title"]) if following else None,
    }
