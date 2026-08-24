from research_dashboard.provenance import (
    event_provenance,
    evidence_provenance,
    path_uri,
)


def test_generic_path_and_evidence_provenance_are_portable(tmp_path):
    locator = tmp_path / "results" / "summary.csv"
    evidence = {
        "evidence_type": "file",
        "locator": str(locator),
        "authority": 2,
        "availability": "available",
        "observed_at": "2026-08-24T12:00:00+00:00",
        "ignored": "not provenance",
    }

    assert path_uri(locator) == locator.absolute().as_uri()
    assert evidence_provenance(evidence) == {
        "evidence_type": "file",
        "locator": str(locator),
        "authority": 2,
        "availability": "available",
        "observed_at": "2026-08-24T12:00:00+00:00",
    }


def test_event_provenance_omits_absent_optional_source_values():
    assert event_provenance(
        {"source_agent": None, "source_session": "example-session"}
    ) == {"source_session": "example-session"}
