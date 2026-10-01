import pytest


def test_latest_project_updates_use_observation_date_and_latest_category():
    from research_dashboard.presentation import project_updates

    def event(date, sequence, importance):
        return dict(observed_at=date, sequence=sequence, importance=importance, new_state=None)
    old = event("2026-08-01T00:00:00+00:00", 100, "Critical blocker")
    recent = event("2026-09-19T00:00:00+00:00", 2, "Routine change")
    decision = event("2026-09-18T00:00:00+00:00", 3, "Decision needed")
    rows = project_updates([
        dict(project={"name": "A", "lifecycle": "Active"}, timeline=[recent, old]),
        dict(project={"name": "B", "lifecycle": "Active"}, timeline=[decision]),
        dict(project={"name": "C", "lifecycle": "Active"}, timeline=[]),
    ])
    assert [row["project"]["name"] for row in rows] == ["A", "B", "C"]
    assert rows[0]["update"] == recent
    assert [row["category"] for row in rows] == ["regular", "needs-me", "regular"]


@pytest.mark.parametrize(("raw", "expected"), [
    ("Job 1234567 completed; independent validation is pending.",
     "Job completed; independent validation is pending."),
    ("Prepare job 12345678 failed and preflight job 23456789 was cancelled.",
     "Prepare job failed and preflight job was cancelled."),
    ("Repair v2 array 3456789 covers nine cells; 145 of 480 bundles committed.",
     "Repair v2 array covers nine cells; 145 of 480 bundles committed."),
    ("Commit bc8f63d2fd6ff3a1dd2ab01234567890abcdef1234 failed validation.",
     "Commit [fingerprint] failed validation."),
    ("150 datasets, 600 artifacts; p=0.001. This is not a scientific PASS.",
     "150 datasets, 600 artifacts; p=0.001. This is not a scientific PASS."),
    ("Tasks 375, 451, and 478; 2026-09-19; X1/X2; 0.396 GB.",
     "Tasks 375, 451, and 478; 2026-09-19; X1/X2; 0.396 GB."),
])
def test_readable_text_suppresses_identifiers_without_changing_findings(raw, expected):
    from research_dashboard.presentation import readable_text

    assert readable_text(raw) == expected
