import json
from pathlib import Path

from scripts.check_portability import find_portability_issues

from research_dashboard.domain import SemanticEventInput
from research_dashboard.settings import Settings
from research_dashboard.web import create_app


REMOVED_PATHS = (
    "src/research_dashboard/actions.py",
    f"src/research_dashboard/{'codex'}_worker.py",
    "src/research_dashboard/investigations.py",
    "src/research_dashboard/corrections.py",
    "src/research_dashboard/integration.py",
    "src/research_dashboard/integration",
    "integration",
    "scripts/install_runtime.py",
    "scripts/install_launch_agent.py",
    "src/research_dashboard/hpc.py",
)


def test_private_workflow_paths_are_absent():
    for path in REMOVED_PATHS:
        assert not Path(path).exists(), path


def test_package_data_does_not_include_removed_workflow_assets():
    package_data = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"integration/*.json"' not in package_data


def test_dashboard_http_routes_are_read_only(tmp_path):
    application = create_app(Settings(tmp_path / "runtime"))
    mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}

    assert not [
        route.path
        for route in application.routes
        if mutation_methods.intersection(getattr(route, "methods", set()))
    ]


def test_public_examples_are_parseable_and_event_examples_validate():
    example_files = sorted(Path("examples").glob("**/*.json"))

    assert example_files
    examples = {
        path: json.loads(path.read_text(encoding="utf-8")) for path in example_files
    }

    assert all(isinstance(payload, dict) for payload in examples.values())
    SemanticEventInput.model_validate(examples[Path("examples/basic/event.json")])
    SemanticEventInput.model_validate(examples[Path("examples/codex/example-event.json")])


def test_public_examples_have_local_available_evidence():
    for event_path in (
        Path("examples/basic/event.json"),
        Path("examples/codex/example-event.json"),
    ):
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        for evidence in payload["evidence"]:
            if evidence["availability"] == "available":
                assert Path(evidence["locator"]).is_file(), evidence["locator"]


def test_non_activated_venv_documentation_uses_the_explicit_cli_path():
    for path in (
        Path("README.md"),
        Path("examples/codex/README.md"),
        Path("examples/slurm/README.md"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "research-dashboard" in text
        assert ".venv/bin/research-dashboard" in text


def test_portability_scanner_rejects_synthetic_absolute_user_home(tmp_path):
    (tmp_path / "notes.md").write_text(
        "Use /" + "Users/example-user/private-project only for this synthetic test.\n",
        encoding="utf-8",
    )

    issues = find_portability_issues(tmp_path)

    assert any("absolute user-home path" in issue.message for issue in issues)


def test_portability_scanner_allows_documented_runtime_default(tmp_path):
    (tmp_path / "runtime.md").write_text(
        "Runtime state defaults to ~/.research-dashboard/.\n", encoding="utf-8"
    )

    assert find_portability_issues(tmp_path) == []


def test_portability_scanner_does_not_treat_a_cloud_directory_name_as_a_path(tmp_path):
    (tmp_path / "notes.md").write_text(
        'The literal "CloudStorage/" is not a filesystem path.\n', encoding="utf-8"
    )

    assert find_portability_issues(tmp_path) == []


def test_portability_scanner_rejects_obvious_credential_assignment(tmp_path):
    (tmp_path / "settings.txt").write_text(
        'pass' + 'word = "synthetic-credential-value"\n', encoding="utf-8"
    )

    issues = find_portability_issues(tmp_path)

    assert any("credential assignment" in issue.message for issue in issues)


def test_portability_scanner_rejects_internal_execution_records(tmp_path):
    record = tmp_path / ".superpowers" / "sdd" / "record.md"
    record.parent.mkdir(parents=True)
    record.write_text(
        "Path: /" + "Users/example-user/private-project\n", encoding="utf-8"
    )

    issues = find_portability_issues(tmp_path)

    assert any(
        issue.path == Path(".superpowers/sdd/record.md")
        and issue.message == "absolute user-home path"
        for issue in issues
    )


def test_portability_scanner_rejects_non_synthetic_fixture_runtime_files(tmp_path):
    fixture = tmp_path / "tests" / "fixtures" / "real.sqlite"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("not a synthetic fixture\n", encoding="utf-8")

    issues = find_portability_issues(tmp_path)

    assert any(
        issue.path == Path("tests/fixtures/real.sqlite")
        and "runtime database or log file" in issue.message
        for issue in issues
    )


def test_portability_scanner_accepts_only_the_exact_synthetic_fixture_root(tmp_path):
    fixture = tmp_path / "tests" / "fixtures" / "synthetic" / "example.sqlite"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("synthetic fixture marker\n", encoding="utf-8")

    assert find_portability_issues(tmp_path) == []


def test_portability_scanner_rejects_windows_absolute_user_home(tmp_path):
    (tmp_path / "notes.md").write_text(
        "C:" + "\\Users\\example-user\\private-project\n", encoding="utf-8"
    )

    issues = find_portability_issues(tmp_path)

    assert any("absolute user-home path" in issue.message for issue in issues)


def test_portability_scanner_rejects_windows_app_data_and_agent_config(tmp_path):
    (tmp_path / "notes.md").write_text(
        "C:"
        + "\\Users\\example-user\\AppData\\Roaming\\legacy-tool\n"
        + "C:"
        + "\\Users\\example-user\\"
        + ".agents\\settings.json\n",
        encoding="utf-8",
    )

    messages = {issue.message for issue in find_portability_issues(tmp_path)}

    assert "platform-specific application-data path" in messages
    assert "local agent configuration path" in messages


def test_portability_scanner_rejects_dsa_private_key_material(tmp_path):
    (tmp_path / "key.pem").write_text(
        "-----BEGIN " + "DSA PRIVATE KEY-----\n", encoding="utf-8"
    )

    issues = find_portability_issues(tmp_path)

    assert any("private-key material" in issue.message for issue in issues)


def test_removed_source_integration_and_installer_paths_are_absent():
    for path in REMOVED_PATHS:
        assert not Path(path).exists(), path
