"""Generic path and evidence provenance helpers."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any


_EVIDENCE_PROVENANCE_FIELDS = (
    "evidence_type",
    "locator",
    "authority",
    "availability",
    "observed_at",
)

_EVENT_PROVENANCE_FIELDS = (
    "source_agent",
    "source_session",
)


def path_uri(path: str | Path) -> str:
    """Return a portable file URI for a registered local path."""
    return Path(path).expanduser().absolute().as_uri()


def evidence_provenance(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return the generic provenance fields carried by one evidence record."""
    return {
        field: evidence[field]
        for field in _EVIDENCE_PROVENANCE_FIELDS
        if field in evidence
    }


def event_provenance(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the optional provenance fields recorded for one event."""
    return {
        field: event[field]
        for field in _EVENT_PROVENANCE_FIELDS
        if event.get(field) is not None
    }
