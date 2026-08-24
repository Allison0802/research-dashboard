# Public Portability Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Clean-history override:** Tasks 1-9 MUST NOT run `git init`, `git add`, `git commit`, create a GitHub repository, or otherwise create Git history. The normal per-task commit step from the planning workflow is intentionally replaced by verification checkpoints. Task 10 alone owns Git initialization and publication after the sanitized candidate passes the local release gates.

**Goal:** Refactor the current private Research Dashboard into a clean, empty-by-default, developer-oriented framework for visualizing agent-assisted research work, with arbitrary domains, agent-neutral semantic events, backend-neutral executions, optional Slurm support, no Codex runtime dependency, no private machine assumptions, and a fresh public Git history only after privacy and release verification pass.

**Architecture:** Keep the existing SQLite + Pydantic + FastAPI/Jinja core where it already models generic research state. Delete private portfolio, Codex orchestration, investigation/correction, machine-specific integration, and institution-specific scheduler paths rather than wrapping them. Add only two reusable abstractions justified by the accepted design: lightweight agent provenance on semantic events and backend-neutral execution persistence; Slurm is an optional adapter over that execution model.

**Tech Stack:** Python 3.11+, SQLite, Pydantic 2, FastAPI, Jinja2, Uvicorn, Pytest, standard-library `venv`, `pip`, and `subprocess`. No new runtime dependency is required.

**Local design authority:** `Research Dashboard Public Portability Design Consensus.md`

**Public spec produced by Task 1:** `docs/superpowers/specs/2026-08-24-public-portability-design.md`

## Execution contract

- Use `python3` for source-tree Python commands. Do not assume a `python` executable exists.
- Use the absolute interpreter inside a verification virtual environment for installed-wheel checks.
- Do not call Python through a deliberately restricted `PATH`; use `sys.executable` or an absolute venv interpreter instead.
- Each task must finish with the package importable and its affected focused tests green. Transitional coexistence of old and new code is allowed only where a later task removes the old path; do not add compatibility aliases or migration layers.
- If a named file has already been removed, verify that its obsolete responsibility is absent; do not recreate it.
- Do not preserve old private database compatibility. Tests use fresh databases created from the current `schema.sql`.
- Do not create speculative plugin/provider/config abstractions.

## Global constraints

- Public v0.1 supports macOS and Linux with Python 3.11+.
- A fresh database contains zero **user/research application records**. The retained `review_state` singleton is initialization metadata and may contain exactly one row with `singleton=1` and `reviewed_through_sequence=0`; every project/event/risk/planning/execution/checkpoint/evidence table must be empty.
- `domain` is an arbitrary non-empty string.
- The four-project onboarding contract and every fixed portfolio/domain query are deleted, not generalized.
- Codex is not a package/runtime requirement and is not launched by the dashboard.
- Slurm is optional and contains no institution-specific scheduler behavior.
- No backward compatibility or private-database migration path is preserved.
- Default runtime root is `~/.research-dashboard/`; `RESEARCH_DASHBOARD_HOME` is the only runtime-root override.
- No fixed user-specific absolute production paths.
- No general plugin/provider/config framework.
- No HTTP mutation API.
- No automatic demo/seed data.
- No `.git` directory may exist before Task 10.
- The one-time private denylist remains outside the repository.
- The public repository is `Allison0802/research-dashboard`, version `0.1.0`, MIT licensed.
- GitHub Actions must use the current official stable majors at implementation time; verify official action documentation immediately before writing CI rather than hard-coding a stale major from this plan.
- Delete obsolete paths instead of adding compatibility aliases, migrations, deprecated commands, or fallback routes.

---

## File responsibility map

### Keep and generalize

- `src/research_dashboard/settings.py` — runtime root only.
- `src/research_dashboard/db.py` — SQLite connection, schema validation, snapshots.
- `src/research_dashboard/domain.py` — public Pydantic domain inputs and normalized literals.
- `src/research_dashboard/registry.py` — generic projects, roots, governing plans.
- `src/research_dashboard/events.py` — append-only semantic event ingestion.
- `src/research_dashboard/state.py` — derived project/portfolio state with no private-domain special cases.
- `src/research_dashboard/risks.py` — generic risk state.
- `src/research_dashboard/reviews.py` — review/checkpoint state.
- `src/research_dashboard/planning.py` — roadmap/TODO mutations.
- `src/research_dashboard/plan_sync.py` — conservative governing-plan bootstrap.
- `src/research_dashboard/provenance.py` — generic path/evidence provenance only.
- `src/research_dashboard/cli.py` — generic CLI plus explicit Slurm namespace.
- `src/research_dashboard/web.py` — read-oriented generic dashboard and supported user state actions.
- `src/research_dashboard/schema.sql` — only the current public schema.
- `src/research_dashboard/templates/` and `src/research_dashboard/static/` — generic reference UI.

### Create

- `docs/superpowers/specs/2026-08-24-public-portability-design.md`
- `src/research_dashboard/executions.py`
- `src/research_dashboard/adapters/__init__.py`
- `src/research_dashboard/adapters/slurm.py`
- `tests/test_settings.py`
- `tests/test_executions.py`
- `tests/test_slurm_adapter.py`
- `tests/test_public_contract.py`
- `scripts/check_portability.py`
- `examples/basic/project.json`
- `examples/basic/event.json`
- `examples/codex/README.md`
- `examples/codex/example-event.json`
- `examples/slurm/README.md`
- `docs/event-contract.md`
- `docs/extending.md`
- `.github/workflows/ci.yml`
- `LICENSE`

### Delete before release

- `src/research_dashboard/onboarding.py`
- `src/research_dashboard/actions.py`
- `src/research_dashboard/codex_worker.py`
- `src/research_dashboard/investigations.py`
- `src/research_dashboard/corrections.py`
- `src/research_dashboard/integration.py`
- `src/research_dashboard/hpc.py`
- `src/research_dashboard/integration/`
- top-level `integration/`
- `scripts/install_runtime.py`
- `scripts/install_launch_agent.py`
- tests that exist only for the deleted responsibilities
- `src/research_dashboard/templates/action.html`
- the root-local `Research Dashboard Public Portability Design Consensus.md` after Task 1 has produced the sanitized public spec and before release scanning; it is implementation authority, not a public artifact.

---

### Task 1: Materialize the sanitized public design, freeze the interpreter contract, and establish the external privacy baseline

**Files:**
- Read: `Research Dashboard Public Portability Design Consensus.md`
- Create: `docs/superpowers/specs/2026-08-24-public-portability-design.md`
- No product code changes.

**Interfaces:**
- Consumes: the accepted design consensus.
- Produces: the sanitized public spec used by Tasks 2-10 and `/tmp/research-dashboard-public-release/private-denylist.txt` used by the final private-information scan.

- [ ] **Step 1: Verify the clean-history and interpreter preconditions**

Run:

```bash
test ! -e .git
python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version; print(sys.executable); print(sys.version)'
```

Expected: both commands exit `0`. If `.git` exists, stop release work and report the discrepancy; do not delete or rewrite Git metadata automatically.

- [ ] **Step 2: Create the sanitized public spec from the consensus**

Create `docs/superpowers/specs/2026-08-24-public-portability-design.md` that preserves the accepted product, architecture, deletion, execution, Slurm, planning, UI, privacy, fresh-history, CI, and release contracts.

Apply these operational clarifications while materializing it:

1. use `python3` in developer instructions;
2. define an empty database as zero user/research records while allowing exactly the initialized `review_state` singleton;
3. do not reproduce the private macOS user-home example from the local consensus; describe the prohibition generically;
4. do not freeze GitHub Action major versions in the spec; require verification against official documentation at implementation time;
5. state that fixed domain-specific portfolio queries are part of the obsolete private portfolio model and must be removed;
6. state that observation replay is idempotent for exact duplicates and that the current execution summary follows the most recently accepted observation in ingestion order.

The public spec must not contain any known private name, email, host, private project identifier, real scheduler ID, cloud path, or user-home prefix.

- [ ] **Step 3: Verify the public spec has no unresolved markers or private-path syntax**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("docs/superpowers/specs/2026-08-24-public-portability-design.md")
text = p.read_text(encoding="utf-8")
assert "TBD:" not in text
assert "FIXME:" not in text
assert "<placeholder>" not in text
assert ("/" + "Users/") not in text
assert ("Library/" + "CloudStorage/") not in text
assert "Allison0802/research-dashboard" in text
assert "~/.research-dashboard/" in text
print("public spec contract: PASS")
PY
```

Expected: `public spec contract: PASS`.

- [ ] **Step 4: Prepare the one-time external private denylist before deleting private source paths**

Create `/tmp/research-dashboard-public-release/private-denylist.txt` outside the repository. Format is UTF-8, one exact sensitive literal per line; blank lines and lines beginning with `#` are ignored.

Populate it by reading the current private candidate before deletions. It must include every observed literal in these categories:

- private personal names/usernames other than the explicitly approved public GitHub handle `Allison0802`;
- private home-directory prefixes;
- private cloud-storage roots;
- private project names and IDs from the old onboarding/portfolio contract;
- inherited institutional/private email addresses and URLs;
- private hostnames/SSH targets;
- real scheduler job IDs that appear in source/tests/docs;
- machine-specific integration paths.

Inspect at minimum `onboarding.py`, `integration.py`, `hpc.py`, top-level `integration/`, README/docs, and their tests before those paths are removed. Do **not** put this denylist in the repository.

The approved public handle `Allison0802` and public repository URL are deliberately omitted from the denylist.

- [ ] **Step 5: Record the current test baseline with the correct interpreter**

Run:

```bash
python3 -m pytest -q
```

Expected: record the actual baseline. Existing private tests may pass or fail; this is evidence only and does not block the refactor.

- [ ] **Step 6: Re-verify no Git history exists**

Run `test ! -e .git` and require exit `0`.

---

### Task 2: Make runtime/project state portable and remove the fixed portfolio/domain contract

**Files:**
- Modify: `src/research_dashboard/settings.py`
- Modify: `src/research_dashboard/domain.py`
- Modify: `src/research_dashboard/schema.sql`
- Modify: `src/research_dashboard/registry.py`
- Modify: `src/research_dashboard/state.py`
- Modify: `src/research_dashboard/cli.py`
- Delete: `src/research_dashboard/onboarding.py`
- Delete: `tests/test_onboarding.py`
- Create: `tests/test_settings.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_state.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_db.py`
- Modify: `README.md`

**Interfaces:**
- Produces `DEFAULT_RUNTIME_ROOT = Path.home() / ".research-dashboard"`.
- Produces `ProjectInput.domain: str`.
- Keeps generic `project add`, root registration, plan registration, and generic portfolio queries.
- Removes the onboarding manifest contract and every fixed/private-domain-specific portfolio query.

- [ ] **Step 1: Add failing portable-settings tests**

Create `tests/test_settings.py`:

```python
from pathlib import Path

from research_dashboard.settings import load_settings


def test_default_runtime_root_uses_home(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_DASHBOARD_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert load_settings().runtime_root == tmp_path / ".research-dashboard"


def test_runtime_root_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "runtime"
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(custom))
    assert load_settings().runtime_root == custom
```

Run `python3 -m pytest tests/test_settings.py -q` and verify the default-path test fails before implementation.

- [ ] **Step 2: Add failing arbitrary-domain and unlimited-project tests**

Add to `tests/test_registry.py`:

```python
def test_project_accepts_arbitrary_domain(connection):
    project = add_project(
        connection,
        {
            "project_id": "climate-forecasting",
            "name": "Climate Forecasting",
            "domain": "Climate Science",
        },
    )
    assert project["domain"] == "Climate Science"


def test_registry_supports_more_than_four_projects(connection):
    for i in range(6):
        add_project(
            connection,
            {
                "project_id": f"project-{i}",
                "name": f"Project {i}",
                "domain": f"Domain {i}",
            },
        )
    assert len(list_projects(connection)) == 6
```

Run `python3 -m pytest tests/test_registry.py -q` and verify arbitrary-domain validation fails before implementation.

- [ ] **Step 3: Add tests proving the private fixed-domain portfolio query is gone**

In `tests/test_state.py`, delete the tests for the existing query whose implementation filters one hard-coded domain. Freeze the remaining public dispatcher contract explicitly:

```python
from research_dashboard.state import PORTFOLIO_QUERY_NAMES


def test_portfolio_query_names_match_public_generic_contract():
    assert PORTFOLIO_QUERY_NAMES == (
        "blocked_today",
        "changed_since_monday",
        "completed_this_week",
        "waiting_on_me",
        "new_research_risks",
    )
```

In `tests/test_cli.py`, ensure the CLI no longer presents `portfolio validate` and no documentation/help fixture advertises a fixed-domain portfolio query.

- [ ] **Step 4: Implement the portable runtime root**

In `settings.py`:

```python
DEFAULT_RUNTIME_ROOT = Path.home() / ".research-dashboard"
```

Keep only the existing `RESEARCH_DASHBOARD_HOME` override.

- [ ] **Step 5: Replace the domain enum and schema CHECK**

Delete the fixed `Domain` enum. Define:

```python
class ProjectInput(_DomainModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    context_path: str | None = None
    lifecycle: Lifecycle = "Needs classification"
    update_horizon_minutes: int | None = Field(default=None, ge=0)
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    updated_at: AwareDatetime = Field(default_factory=_utc_now)
```

In `schema.sql`, make `projects.domain` plain `TEXT NOT NULL` with no fixed-domain CHECK. In `registry.py`, persist `value.domain`, not `value.domain.value`.

- [ ] **Step 6: Delete the onboarding contract and fixed-domain state service**

Delete `onboarding.py`, `test_onboarding.py`, `portfolio validate`, manifest arguments/imports, the existing fixed-domain query function, its natural-language alias, its dispatcher entry, and its tests. Identify it from the current `PORTFOLIO_QUERY_NAMES`/dispatcher as the service whose implementation filters one hard-coded domain. Do not replace it with a generalized portfolio-membership layer or query language.

- [ ] **Step 7: Update README developer installation**

Use the conventional flow from the consensus:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
research-dashboard init
research-dashboard serve
```

Do not document machine-specific daemon installers or private machine paths.

- [ ] **Step 8: Run the complete affected slice**

Run:

```bash
python3 -m pytest tests/test_settings.py tests/test_registry.py tests/test_state.py tests/test_cli.py tests/test_db.py -q
```

Expected: PASS and `python3 -c 'import research_dashboard.cli; import research_dashboard.state'` exits `0`.

---

### Task 3: Delete Codex orchestration, specialized investigation/correction workflows, and machine-specific integration

**Files:**
- Delete: `src/research_dashboard/actions.py`
- Delete: `src/research_dashboard/codex_worker.py`
- Delete: `src/research_dashboard/investigations.py`
- Delete: `src/research_dashboard/corrections.py`
- Delete: `src/research_dashboard/integration.py`
- Delete: `src/research_dashboard/integration/`
- Delete: top-level `integration/`
- Delete: `src/research_dashboard/templates/action.html`
- Delete: `scripts/install_runtime.py`
- Delete: `scripts/install_launch_agent.py`
- Delete: specialized tests for those paths
- Modify: `src/research_dashboard/domain.py`
- Modify: `src/research_dashboard/settings.py`
- Modify: `src/research_dashboard/db.py`
- Modify: `src/research_dashboard/schema.sql`
- Modify: `src/research_dashboard/cli.py`
- Modify: `src/research_dashboard/web.py`
- Modify: `src/research_dashboard/risks.py` only if imports require cleanup
- Modify: `tests/test_events.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_db.py`
- Modify: `pyproject.toml`
- Create/extend: `tests/test_public_contract.py`

**Interfaces:**
- Core retains projects/events/evidence/state/risks/reviews/planning.
- No agent-launching, worker lifecycle, investigation record, correction-request, hook installation, Codex action, or private integration API remains.

- [ ] **Step 1: Write a failing deletion-boundary test**

Create/extend `tests/test_public_contract.py`:

```python
from pathlib import Path

REMOVED_PATHS = (
    "src/research_dashboard/actions.py",
    "src/research_dashboard/codex_worker.py",
    "src/research_dashboard/investigations.py",
    "src/research_dashboard/corrections.py",
    "src/research_dashboard/integration.py",
    "src/research_dashboard/integration",
    "integration",
    "scripts/install_runtime.py",
    "scripts/install_launch_agent.py",
)


def test_private_workflow_paths_are_absent():
    for path in REMOVED_PATHS:
        assert not Path(path).exists(), path
```

Run `python3 -m pytest tests/test_public_contract.py -q` and verify it fails before deletion.

- [ ] **Step 2: Remove obsolete domain types at the same boundary**

Delete from `domain.py` any types used only by the removed workflows, including `CorrectionStatus`, `CorrectionRequestInput`, and `InvestigationInput` if no retained caller remains. Update `tests/test_events.py` so generic model-validation tests no longer import or instantiate those deleted types.

Do not leave dead public models for deleted features.

- [ ] **Step 3: Remove Codex/investigation/correction/integration CLI and web paths**

Delete parser/dispatch/import paths for `integration`, `investigation`, `correction`, Codex worker actions, reconciliation actions backed by the removed worker, and Codex-specific UI labels/buttons.

Retain generic review and risk actions. `context_actions()` must never emit a Codex or generic placeholder agent-launch label.

- [ ] **Step 4: Remove obsolete schema tables and runtime directories**

Delete from `schema.sql` and `REQUIRED_SCHEMA_TABLES`:

```text
investigations
agent_actions
reconciliation_inputs
correction_requests
```

After worker deletion, remove `Settings.action_root`, `Settings.log_root`, `action_timeout_seconds` if no retained caller uses it, and unconditional creation of deleted worker directories in `connect_db()`.

No migration statements are added.

- [ ] **Step 5: Remove deleted integration package data**

In `pyproject.toml`, remove the obsolete package-data entry:

```toml
"integration/*.json",
```

Do not replace it with an empty package or compatibility asset.

- [ ] **Step 6: Update web and CLI tests**

For an active risk, assert the rendered UI contains none of:

```text
Investigate with Codex
Ask Codex to fix
Reconcile project
Investigate with agent
Ask agent to fix
```

Ensure deleted CLI commands are invalid parser choices.

- [ ] **Step 7: Search for stale private-workflow symbols**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

forbidden = (
    "codex_worker",
    "validate_portfolio_onboarding",
    "correction_requests",
    "reconciliation_inputs",
    "agent_actions",
    "Investigate with Codex",
    "Ask Codex to fix",
)
hits = []
for root in (Path("src"), Path("tests")):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".html", ".js", ".css", ".sql", ".json"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append((str(path), token))
assert not hits, hits
print("removed private workflow symbols: PASS")
PY
```

- [ ] **Step 8: Run the affected slice and import smoke**

Run:

```bash
python3 -m pytest tests/test_public_contract.py tests/test_events.py tests/test_web.py tests/test_cli.py tests/test_db.py tests/test_risks.py tests/test_reviews.py -q
python3 -c 'import research_dashboard; import research_dashboard.cli; import research_dashboard.web'
```

Expected: PASS.

---

### Task 4: Add agent-neutral semantic-event provenance

**Files:**
- Modify: `src/research_dashboard/domain.py`
- Modify: `src/research_dashboard/schema.sql`
- Modify: `src/research_dashboard/events.py`
- Modify: `src/research_dashboard/state.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_state.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- `SemanticEventInput.source_agent: str | None`.
- `SemanticEventInput.source_session: str | None` remains supported.
- Both fields round-trip through persistence and derived event views.
- No agent registry is created.

- [ ] **Step 1: Add failing provenance round-trip test**

```python
def test_event_round_trips_agent_provenance(connection, semantic_event):
    payload = {
        **semantic_event,
        "source_agent": "example-agent",
        "source_session": "session-001",
    }
    result = ingest_event(connection, payload)
    assert result["event"]["source_agent"] == "example-agent"
    assert result["event"]["source_session"] == "session-001"
```

Run `python3 -m pytest tests/test_events.py -q` and verify failure before implementation.

- [ ] **Step 2: Add the fields to model/schema**

```python
source_agent: str | None = Field(default=None, min_length=1)
source_session: str | None = Field(default=None, min_length=1)
```

Add nullable `source_agent TEXT` beside `source_session` in `events`.

- [ ] **Step 3: Update every event SELECT/INSERT/identity payload consistently**

Update `_EVENT_COLUMNS` in both `events.py` and `state.py`, insertion values, replay identity checking, event projections, and CLI JSON ingestion tests. Do not create an `agents` table.

- [ ] **Step 4: Run provenance/event/state/CLI tests**

Run:

```bash
python3 -m pytest tests/test_events.py tests/test_state.py tests/test_cli.py -q
```

Expected: PASS and package imports remain green.

---

### Task 5: Add backend-neutral execution persistence while the old HPC path remains temporarily runnable

**Files:**
- Modify: `src/research_dashboard/domain.py`
- Modify: `src/research_dashboard/schema.sql`
- Modify: `src/research_dashboard/db.py`
- Create: `src/research_dashboard/executions.py`
- Create: `tests/test_executions.py`
- Modify: `tests/test_db.py`

**Interfaces:**

```python
ExecutionState = Literal[
    "SUBMITTED", "QUEUED", "RUNNING", "COMPLETED",
    "FAILED", "CANCELLED", "UNKNOWN",
]
```

```python
class ExecutionInput(_DomainModel):
    execution_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    current_state: ExecutionState = "SUBMITTED"
    project_id: str | None = Field(default=None, min_length=1)
    workstream: str | None = Field(default=None, min_length=1)
    task_key: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    last_observed_at: AwareDatetime | None = None
    active: bool = True
```

```python
class ExecutionObservationInput(_DomainModel):
    observation_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    state: ExecutionState
    observed_at: AwareDatetime = Field(default_factory=_utc_now)
    raw_record: dict[str, object]
```

```python
def register_execution(connection, execution) -> dict[str, Any]: ...
def record_execution_observation(connection, observation) -> dict[str, Any]: ...
def list_executions(connection, *, active_only: bool = False) -> list[dict[str, Any]]: ...
```

**Frozen observation semantics:**
- observations are append-only;
- canonical `raw_record` JSON uses `sort_keys=True, separators=(",", ":")`;
- an exact duplicate `(execution_id, state, canonical_raw_record)` is an idempotent no-op that returns the already stored observation and does not create a second row;
- every genuinely new accepted observation becomes the current summary in ingestion order; `observed_at` is evidence metadata, not conflict-resolution ordering;
- `active=False` exactly when the current state is `COMPLETED`, `FAILED`, or `CANCELLED`; a later accepted nonterminal observation may make it active again;
- `UNKNOWN` never means success.

- [ ] **Step 1: Write execution registration tests**

Cover standalone execution, project-linked execution, missing referenced project rejection, and `UNIQUE(backend, external_id)`.

Use `backend="example"` in core tests; core execution tests must not require Slurm.

- [ ] **Step 2: Write exact observation-semantics tests**

Include tests equivalent to:

```python
def test_exact_observation_replay_is_idempotent(connection, execution):
    first = record_execution_observation(connection, observation)
    second = record_execution_observation(connection, observation)
    assert second["observation_id"] == first["observation_id"]
    assert connection.execute(
        "SELECT COUNT(*) FROM execution_observations WHERE execution_id = ?",
        (execution["execution_id"],),
    ).fetchone()[0] == 1


def test_latest_accepted_observation_controls_summary(connection, execution):
    record_execution_observation(connection, completed_observation)
    assert list_executions(connection)[0]["active"] == 0
    record_execution_observation(connection, running_observation)
    current = list_executions(connection)[0]
    assert current["current_state"] == "RUNNING"
    assert current["active"] == 1
```

- [ ] **Step 3: Add execution tables without removing old HPC tables yet**

Add `executions` and `execution_observations` exactly to current `schema.sql` and add them to `REQUIRED_SCHEMA_TABLES`. Keep old HPC tables/types only until Task 6 so the package remains runnable throughout this task.

This is transitional coexistence inside the uncommitted refactor, not a compatibility layer. Task 6 deletes the obsolete HPC contract.

- [ ] **Step 4: Implement models and `executions.py`**

Use the existing connection-validation and `transaction()` patterns. Before inserting an observation, canonicalize `raw_record`; if the unique replay key already exists, return that row unchanged. Otherwise insert and update the execution summary in the same transaction.

- [ ] **Step 5: Run execution + existing package tests**

Run:

```bash
python3 -m pytest tests/test_executions.py tests/test_db.py tests/test_hpc.py tests/test_registry.py tests/test_cli.py -q
python3 -c 'import research_dashboard.cli; import research_dashboard.executions'
```

Expected: PASS. Old HPC still works only because it has not yet been deleted; no new compatibility alias is introduced.

---

### Task 6: Atomically switch from private HPC to the optional Slurm adapter and new execution CLI

**Files:**
- Create: `src/research_dashboard/adapters/__init__.py`
- Create: `src/research_dashboard/adapters/slurm.py`
- Create: `tests/test_slurm_adapter.py`
- Delete: `src/research_dashboard/hpc.py`
- Delete: `tests/test_hpc.py`
- Modify: `src/research_dashboard/domain.py`
- Modify: `src/research_dashboard/registry.py`
- Modify: `src/research_dashboard/schema.sql`
- Modify: `src/research_dashboard/db.py`
- Modify: `src/research_dashboard/cli.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_public_contract.py`

**Interfaces:**

```python
def validate_slurm_job_id(value: str) -> str: ...
def validate_ssh_target(value: str) -> str: ...
def parse_slurm_record(record: str | Mapping[str, Any], *, source: Literal["squeue", "sacct"]) -> dict[str, Any]: ...
def poll_slurm_execution(connection, *, execution_id: str, ssh_target: str, runner=ssh_runner) -> dict[str, Any]: ...
```

CLI after this task:

```text
research-dashboard execution list [--active-only]
research-dashboard slurm register --execution-id ... --job-id ... [project/workstream/task options]
research-dashboard slurm poll --execution-id ... --ssh-target ...
```

Removed permanently:

```text
cluster
job
poll-hpc
```

- [ ] **Step 1: Port the complete scheduler-generic Slurm state normalization contract**

Tests must retain generic behavior from old `hpc.py`, including at least:

```text
PD/PENDING/CONFIGURING/REQUEUED -> QUEUED
R/RUNNING/S/SUSPENDED/CG/COMPLETING -> RUNNING
CD/COMPLETED -> COMPLETED
F/FAILED/BOOT_FAIL/DEADLINE/NODE_FAIL/OUT_OF_MEMORY/PREEMPTED/TIMEOUT/SPECIAL_EXIT -> FAILED
CA/CANCELLED -> CANCELLED
unknown -> UNKNOWN
trailing '+' normalization remains supported
```

Do not reduce behavior to only the short example list.

- [ ] **Step 2: Add security and deterministic polling tests**

Test numeric job IDs only; reject whitespace, arrays, shell punctuation, or extra tokens. SSH target must be one safe `host` or `user@host` token.

Use a fake runner to cover `squeue` success and empty-queue -> `sacct` fallback. No test invokes real SSH, `squeue`, or `sacct`.

A nonzero scheduler query error raises a Slurm adapter error unless a scheduler-generic, fixture-tested diagnostic justifies accounting fallback. Do not preserve institution-specific login-banner allowlists.

- [ ] **Step 3: Implement the Slurm adapter over executions**

The adapter loads the target execution, requires `backend == "slurm"`, uses its stored `external_id` as the validated numeric job ID, polls the explicit command-time `ssh_target`, parses the record, then calls `record_execution_observation()`.

The generic execution table does not persist SSH target or cluster registry state.

- [ ] **Step 4: Replace CLI boundaries before deleting old imports**

Add `execution list`, `slurm register`, and `slurm poll` parser/dispatch. Import Slurm validation/adapter code lazily inside the Slurm command path so ordinary core import/startup does not load the adapter.

`slurm register` calls:

```python
register_execution(
    connection,
    {
        "execution_id": args.execution_id,
        "backend": "slurm",
        "external_id": args.job_id,
        "current_state": "SUBMITTED",
        "project_id": args.project_id,
        "workstream": args.workstream,
        "task_key": args.task_key,
    },
)
```

- [ ] **Step 5: Delete the complete old HPC/cluster contract in the same task**

Delete `hpc.py` and `test_hpc.py`. Remove from `domain.py` `Scheduler`, `HpcState`, `ClusterInput`, `HpcJobInput`, and old scheduler validators if their implementation has moved to `adapters/slurm.py`.

Remove `add_cluster` and cluster-related imports/tests from `registry.py`/`test_registry.py`. Remove old HPC model coverage from `tests/test_events.py`.

Delete `clusters`, `hpc_jobs`, and `hpc_observations` from `schema.sql` and `REQUIRED_SCHEMA_TABLES`.

Delete old CLI commands/parsers only after the replacement parser/dispatch exists in the same change.

- [ ] **Step 6: Extend deletion tests**

Add `src/research_dashboard/hpc.py` to the permanent absent-path assertions and verify old commands are invalid.

- [ ] **Step 7: Run the complete atomic switch slice**

Run:

```bash
python3 -m pytest tests/test_executions.py tests/test_slurm_adapter.py tests/test_registry.py tests/test_events.py tests/test_cli.py tests/test_db.py tests/test_public_contract.py -q
python3 -c 'import research_dashboard; import research_dashboard.cli; import research_dashboard.web'
```

Expected: PASS. There must be no intermediate state in which `cli.py` imports a deleted `hpc.py` or `registry.py` imports a deleted `ClusterInput`.

---

### Task 7: Make UI/state/planning/provenance fully generic and empty-safe

**Files:**
- Modify: `src/research_dashboard/web.py`
- Modify: `src/research_dashboard/state.py`
- Modify: `src/research_dashboard/templates/index.html`
- Modify: affected project/risk templates
- Modify: `src/research_dashboard/static/app.css` only if required by the retained templates
- Modify: `src/research_dashboard/plan_sync.py`
- Modify: `src/research_dashboard/planning.py`
- Modify: `src/research_dashboard/provenance.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_state.py`
- Modify: `tests/test_plan_sync.py`
- Modify: `tests/test_planning.py`
- Modify: `tests/test_provenance.py`
- Modify: `tests/test_risks.py`
- Modify: `tests/test_reviews.py`

**Interfaces:**
- Homepage and detail views support zero/arbitrary projects and arbitrary domains.
- Event provenance renders only when present.
- Execution state renders backend-neutrally.
- Planning remains deterministic/conservative and optional.

- [ ] **Step 1: Add empty-homepage test**

```python
def test_homepage_renders_with_empty_database(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No projects yet" in response.text
    assert "semantic event" in response.text.lower()
```

- [ ] **Step 2: Add arbitrary-domain/provenance/execution render tests**

Create a synthetic `Climate Science` project, event with `source_agent="example-agent"`, and `backend="example"` execution. Assert all render without a fixed-domain mapping or Slurm-specific label.

- [ ] **Step 3: Replace private path fixtures in retained planning/provenance tests**

Change any hard-coded user home, private cloud-storage path, institution-specific project name, or private governing-plan fixture to `tmp_path` synthetic values.

- [ ] **Step 4: Preserve conservative plan parsing**

Keep a test proving ambiguous prose is not guessed into roadmap items. Planning remains optional and deterministic; do not add AI parsing.

- [ ] **Step 5: Implement only the generic/empty-safe UI changes**

Rules:

- empty homepage explains how to add a project and ingest an event;
- domain is plain arbitrary text;
- event source renders only when present;
- execution state is backend-neutral;
- no missing optional integration blocks startup;
- no wholesale visual redesign;
- do not create a `static/css` directory; use the existing `static/app.css` path if CSS changes are needed.

- [ ] **Step 6: Run the retained-core slice**

Run:

```bash
python3 -m pytest tests/test_web.py tests/test_state.py tests/test_plan_sync.py tests/test_planning.py tests/test_provenance.py tests/test_risks.py tests/test_reviews.py -q
```

Expected: PASS.

---

### Task 8: Add public examples/docs/package hygiene/CI and the permanent portability scanner

**Files:**
- Create: `examples/basic/project.json`
- Create: `examples/basic/event.json`
- Create: `examples/codex/README.md`
- Create: `examples/codex/example-event.json`
- Create: `examples/slurm/README.md`
- Create: `docs/event-contract.md`
- Create: `docs/extending.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `pyproject.toml`
- Modify: existing `.gitignore`
- Create: `LICENSE`
- Create: `.github/workflows/ci.yml`
- Create: `scripts/check_portability.py`
- Modify: `tests/test_public_contract.py`
- Delete before release: root `Research Dashboard Public Portability Design Consensus.md`

**Interfaces:**
- Produces installable `research-dashboard==0.1.0` wheel.
- Produces synthetic public examples only.
- Produces a generic permanent repository-hygiene scanner.
- Produces Ubuntu/macOS Python 3.11 CI with no private dependencies.

- [ ] **Step 1: Add synthetic examples**

`examples/basic/project.json`:

```json
{
  "project_id": "example-climate-model",
  "name": "Example Climate Model",
  "domain": "Climate Science",
  "lifecycle": "Active",
  "update_horizon_minutes": 10080
}
```

Create an event example using a fixed non-sensitive UUID/time, `source_agent="example-agent"`, and only synthetic evidence locators. Codex example shows only external event production -> `research-dashboard event add`; Slurm example uses `user@cluster.example` and job ID `12345`.

No example is automatically imported.

- [ ] **Step 2: Document the public architecture and event contract**

`docs/event-contract.md` documents every `SemanticEventInput` field, evidence semantics, timezone requirements, and provenance. `docs/extending.md` explains where adopters change domain semantics, event producers, templates/CSS, state queries, and optional adapters, and explicitly says there is no plugin SDK.

`docs/architecture.md` must contain no private cloud-storage paths, machine-specific daemon-installation assumptions, local agent-configuration assumptions, institution-specific scheduler behavior, or Codex-worker architecture.

- [ ] **Step 3: Keep package metadata minimal and fix stale package data**

Ensure:

```toml
[project]
name = "research-dashboard"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi", "jinja2", "pydantic>=2", "uvicorn"]

[project.urls]
Repository = "https://github.com/Allison0802/research-dashboard"
```

Retain `[project.optional-dependencies].dev` for testing. Remove any deleted `integration/*.json` package-data entry. Do not add Codex, Slurm, SSH, `platformdirs`, or provider/plugin dependencies.

- [ ] **Step 4: Modify the existing `.gitignore`**

Do not recreate it as a new file. Ensure it includes at least:

```gitignore
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/
*.sqlite
*.sqlite3
*.db
*.log
logs/
.DS_Store
.env
.env.*
.ai-bridge/
.worktrees/
```

- [ ] **Step 5: Create MIT license and verify current GitHub Action majors**

Create the standard MIT license for 2026 and the approved public identity. Before writing `.github/workflows/ci.yml`, verify official `actions/checkout` and `actions/setup-python` documentation and use their then-current stable documented major versions.

CI matrix:

```yaml
os: [ubuntu-latest, macos-latest]
python-version: "3.11"
```

CI steps install `.[dev]`, run full Pytest, run the portability scanner, and build the wheel. CI must not require Codex, Slurm, SSH credentials, private environment variables, or private fixtures.

- [ ] **Step 6: Implement the permanent generic portability scanner**

`scripts/check_portability.py` scans repository text files, excluding `.git`, virtualenvs, caches, and build outputs. It fails on generic patterns such as user-home absolute paths, cloud-storage roots, legacy platform-specific application-data paths, local agent-hook/config paths, obvious credential/token patterns, and runtime DB/log files outside explicitly synthetic test fixtures.

Do not encode actual private names/emails/hosts/project strings in the permanent scanner.

- [ ] **Step 7: Add public-contract tests**

Tests must:

- parse every `examples/**/*.json`;
- validate semantic-event examples through `SemanticEventInput`;
- prove a synthetic absolute user-home path is rejected by the scanner;
- prove `~/.research-dashboard/` documentation is accepted;
- prove removed source/integration/installer paths are absent.

- [ ] **Step 8: Remove the local consensus artifact from the public candidate**

After confirming the sanitized public spec exists and contains the accepted design, delete `Research Dashboard Public Portability Design Consensus.md` from the candidate tree. Do not delete the external `/tmp/.../private-denylist.txt`.

- [ ] **Step 9: Run documentation/package/public-contract verification**

Run:

```bash
python3 -m pytest tests/test_public_contract.py tests/test_events.py -q
python3 scripts/check_portability.py
python3 -m pip wheel . --no-deps -w dist
```

Expected: PASS/build success. Remove `dist/` after this development check; Task 9 performs the authoritative release build.

---

### Task 9: Close development and run the simplified authoritative pre-Git release gates

**Files:**
- Product changes only if a gate exposes a real defect; repair in the owning task's files first.
- Release evidence and one-time privacy artifacts live only under `/tmp/research-dashboard-public-release/`.

**Gate policy:** This task replaces the previous repetitive Task-14/Task-15 gate stack. There are five authoritative local gates. If one fails, repair the owning code test-first, rerun its focused tests, then resume from the earliest gate invalidated by that repair. Do not mechanically restart every earlier gate. Immediately before Task 10, Gate A and Gate E must both be green in the final candidate state.

#### Gate A — Full source suite and permanent portability scan

- [ ] Run:

```bash
python3 -m pytest -q
python3 scripts/check_portability.py
```

Expected: PASS.

- [ ] Verify core import with all optional executables hidden **without relying on Python in the restricted PATH**:

```bash
python3 - <<'PY'
import os
import subprocess
import sys
import tempfile

env = {
    **os.environ,
    "PATH": "/usr/bin:/bin",
    "RESEARCH_DASHBOARD_HOME": tempfile.mkdtemp(prefix="research-dashboard-core-"),
}
subprocess.run(
    [sys.executable, "-c", "import research_dashboard; import research_dashboard.web; import research_dashboard.cli"],
    env=env,
    check=True,
)
print("core optional-executable independence: PASS")
PY
```

Expected: PASS. Core import must not locate or execute Codex, SSH, `squeue`, or `sacct`.

#### Gate B — Authoritative wheel build and clean installation

- [ ] Remove stale build output and build exactly one wheel:

```bash
rm -rf dist build
python3 -m pip wheel . --no-deps -w dist
```

Expected: one `research_dashboard-0.1.0-*.whl`.

- [ ] Create the external verification environment:

```bash
rm -rf /tmp/research-dashboard-public-release/verify
python3 -m venv /tmp/research-dashboard-public-release/verify/venv
/tmp/research-dashboard-public-release/verify/venv/bin/python -m pip install --upgrade pip
/tmp/research-dashboard-public-release/verify/venv/bin/python -m pip install dist/research_dashboard-0.1.0-*.whl
```

Expected: install succeeds without editable mode and without files outside the wheel/repository dependency contract.

#### Gate C — One installed-wheel smoke proves empty state, E2E behavior, and Codex/Slurm absence

- [ ] Initialize the installed wheel under a restricted executable PATH:

```bash
env PATH="/usr/bin:/bin" \
  RESEARCH_DASHBOARD_HOME=/tmp/research-dashboard-public-release/verify/runtime \
  /tmp/research-dashboard-public-release/verify/venv/bin/research-dashboard init
```

This single restricted-PATH installed-wheel flow replaces separate duplicated “Codex absent” and “Slurm absent” gates.

- [ ] Verify the exact empty-database contract:

```bash
/tmp/research-dashboard-public-release/verify/venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path

path = Path("/tmp/research-dashboard-public-release/verify/runtime/dashboard.sqlite3")
con = sqlite3.connect(path)
counts = {
    row[0]: con.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
    for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
}
assert counts.get("review_state") == 1, counts
review = con.execute(
    "SELECT singleton, reviewed_through_sequence, reviewed_at FROM review_state"
).fetchone()
assert review == (1, 0, None), review
for table, count in counts.items():
    if table == "review_state":
        continue
    assert count == 0, (table, count)
print("empty application state: PASS")
PY
```

Expected: PASS.

- [ ] Probe empty UI using a dynamically allocated port, not hard-coded ports:

```bash
RESEARCH_DASHBOARD_HOME=/tmp/research-dashboard-public-release/verify/runtime \
/tmp/research-dashboard-public-release/verify/venv/bin/python - <<'PY'
import os
import socket
import subprocess
import time
import urllib.request

exe = "/tmp/research-dashboard-public-release/verify/venv/bin/research-dashboard"
env = {**os.environ, "PATH": "/usr/bin:/bin", "RESEARCH_DASHBOARD_HOME": "/tmp/research-dashboard-public-release/verify/runtime"}
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
proc = subprocess.Popen([exe, "serve", "--host", "127.0.0.1", "--port", str(port)], env=env)
try:
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                body = response.read().decode()
                assert response.status == 200
                assert "No projects yet" in body
                print("empty dashboard: PASS")
                break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("dashboard did not become ready")
finally:
    proc.terminate()
    proc.wait(timeout=10)
PY
```

- [ ] Run the synthetic installed-wheel E2E with the same restricted PATH:

Add the synthetic project via CLI, ingest a synthetic event JSON under `/tmp/research-dashboard-public-release/verify/`, query state through the existing public CLI/Python state interface, and probe a dynamically allocated UI port. Require the project name, `Climate Science`, and resulting completed task state to be observable. Do not call Slurm commands.

Expected: PASS with no `codex`, `ssh`, `squeue`, or `sacct` available in PATH.

#### Gate D — Slurm adapter fixtures only

- [ ] Run:

```bash
python3 -m pytest tests/test_slurm_adapter.py tests/test_executions.py -q
```

Expected: PASS. No network/SSH use.

#### Gate E — Privacy and candidate-tree release audit

- [ ] Run the permanent scanner:

```bash
python3 scripts/check_portability.py
```

- [ ] Run the external denylist + secret scan using this exact protocol outside the repository:

```bash
python3 - <<'PY'
from pathlib import Path
import re

root = Path(".").resolve()
denylist_path = Path("/tmp/research-dashboard-public-release/private-denylist.txt")
assert denylist_path.is_file(), "external private denylist is missing"

deny = [
    line.strip()
    for line in denylist_path.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
assert deny, "external private denylist is empty"

skip_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"}
text_suffixes = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".html", ".css", ".js", ".sql", ".sh"}
secret_patterns = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "generic api token assignment": re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
}

problems = []
for path in root.rglob("*"):
    rel = path.relative_to(root)
    if any(part in skip_dirs for part in rel.parts) or not path.is_file():
        continue
    if path.suffix.lower() not in text_suffixes and path.name not in {"LICENSE", ".gitignore"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for literal in deny:
        if literal in text:
            problems.append(f"denylist literal in {rel}: {literal!r}")
    for name, pattern in secret_patterns.items():
        if pattern.search(text):
            problems.append(f"{name} pattern in {rel}")

assert not problems, "\n".join(problems)
print("external private-information scan: PASS")
PY
```

- [ ] Remove repository-local generated outputs and audit the candidate tree:

```bash
rm -rf dist build .pytest_cache
find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

Then:

```bash
python3 - <<'PY'
from pathlib import Path

root = Path(".")
blocked_suffixes = {".sqlite", ".sqlite3", ".db", ".log"}
blocked_dirs = {".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build", ".git"}
problems = []
for path in root.rglob("*"):
    rel = path.relative_to(root)
    if any(part in blocked_dirs for part in rel.parts):
        problems.append(f"generated/history directory: {rel}")
        continue
    if path.is_symlink():
        problems.append(f"unexpected symlink: {rel} -> {path.readlink()}")
    if path.is_file() and path.suffix.lower() in blocked_suffixes:
        problems.append(f"runtime/generated file: {rel}")
assert not problems, "\n".join(sorted(set(problems)))
print("candidate tree audit: PASS")
PY
```

- [ ] Final clean-history gate:

```bash
test ! -e .git
```

Expected: exit `0`.

**Final Task-9 rule:** If any source/document/example file changed after its last Gate A or Gate E pass, rerun the affected focused tests, then rerun Gate A and Gate E. Task 10 may begin only when both are green on the exact candidate state being initialized into Git.

---

### Task 10: Create the first clean Git history, publish, verify CI, and release v0.1.0

**Files:**
- Git metadata only after Task 9 PASS.
- No source changes should be required before the first commit.

**Interfaces:**
- Consumes: final Task 9 candidate state.
- Produces clean `main`, public `Allison0802/research-dashboard`, green Ubuntu/macOS CI, tag/release `v0.1.0`.

- [ ] **Step 1: Initialize Git only now**

```bash
git init
git branch -M main
```

- [ ] **Step 2: Stage the complete candidate and inspect it**

```bash
git add .
git status --short
git diff --cached --stat
git diff --cached --check
```

Expected: only sanitized public source/docs/tests/examples/workflow/license/ignore files.

- [ ] **Step 3: Re-run privacy checks against staged files before the first commit**

Run the permanent scanner again. Then run the external denylist/secret scan over only files returned by `git diff --cached --name-only --diff-filter=ACMR` (reuse the Task-9 matching rules). Do not rely only on the working-tree scan.

Expected: zero findings.

- [ ] **Step 4: Create the first commit**

```bash
git commit -m "Initial public release candidate"
```

- [ ] **Step 5: Verify GitHub CLI identity and destination state**

Run:

```bash
gh auth status
gh repo view Allison0802/research-dashboard
```

If the repository does not exist, create it. If it exists, verify it is empty before adding/pushing a remote. Never force-push unrelated history.

- [ ] **Step 6: Create/push the public repository**

If absent:

```bash
gh repo create Allison0802/research-dashboard --public --source=. --remote=origin --push
```

If confirmed empty:

```bash
git remote add origin https://github.com/Allison0802/research-dashboard.git
git push -u origin main
```

Do not push other branches or tags.

- [ ] **Step 7: Require green CI for the pushed commit**

```bash
gh run list --workflow CI --limit 5
```

Resolve the workflow run for the current commit and watch that exact run:

```bash
CURRENT_SHA="$(git rev-parse HEAD)"
RUN_ID="$(gh run list --workflow CI --limit 20 --json databaseId,headSha --jq ".[] | select(.headSha == \"$CURRENT_SHA\") | .databaseId" | head -n 1)"
test -n "$RUN_ID"
gh run watch "$RUN_ID" --exit-status
```

Expected: Ubuntu and macOS jobs both succeed.

If CI finds a normal software defect, fix it on `main`, rerun the affected local tests plus `python3 scripts/check_portability.py`, commit normally, push, and require green CI. Do not rewrite public history merely to preserve a single initial commit.

- [ ] **Step 8: Tag only the green accepted commit**

```bash
git tag -a v0.1.0 -m "Research Dashboard v0.1.0"
git push origin v0.1.0
```

- [ ] **Step 9: Create the GitHub release**

```bash
gh release create v0.1.0 --title "Research Dashboard v0.1.0" --generate-notes
```

- [ ] **Step 10: Final release verification**

```bash
gh repo view Allison0802/research-dashboard
gh run list --workflow CI --limit 3
git status --short
```

Require:

- repository is public;
- accepted release commit has green CI;
- `v0.1.0` exists;
- working tree is clean.

---

## Plan self-review

### Audit findings closed

- Impossible `"TODO" not in spec` gate: removed; placeholder checks use explicit unresolved-marker syntax only.
- Missing `python` executable: all source-tree commands use `python3`; restricted-PATH checks invoke `sys.executable` or absolute venv executables.
- Broken Task-6/7/8 sequencing: replaced with a working two-step transition—Task 5 adds executions while old HPC remains runnable; Task 6 switches CLI/adapter and deletes old HPC atomically.
- `review_state` contradiction: empty-state contract explicitly distinguishes initialization metadata from user/research application rows.
- Undefined `$VERIFY_ROOT`: removed; all release paths are explicit under `/tmp/research-dashboard-public-release/`.
- Plan/spec missing from workspace: this plan is materialized at its canonical path; Task 1 materializes the sanitized public spec from the local consensus.
- Fixed-domain portfolio query: explicitly deleted with the private portfolio contract.
- Obsolete `InvestigationInput`/correction domain types: explicitly deleted with their workflows.
- Execution replay/current-state ambiguity: exact duplicate is idempotent; genuinely new observation updates current summary in ingestion order.
- Slurm state under-specification: full scheduler-generic normalization behavior from old `hpc.py` is retained; only institution-specific behavior is removed.
- Nonexistent `static/css`: corrected to existing `static/app.css`.
- External privacy scan ambiguity: exact denylist format, location, secret patterns, and executable scan protocol are specified.
- Fixed release ports: replaced with dynamically allocated localhost ports.
- Existing `.gitignore`: treated as Modify, not Create.
- Stale `integration/*.json` package data: explicitly removed.

### Gate simplification

The old repeated Task-14/Task-15 verification stack is replaced by five authoritative pre-Git gates:

1. full source suite + permanent portability + optional-executable import independence;
2. authoritative wheel build + clean install;
3. one installed-wheel restricted-PATH smoke covering empty DB/UI, synthetic E2E, Codex absence, and Slurm absence;
4. deterministic Slurm fixture tests;
5. permanent + external privacy scans and candidate-tree audit.

No-Git checks occur only at initial preflight, the final pre-Git gate, and implicitly after Task 10 initialization owns history. Failed gates resume from the earliest invalidated gate rather than restarting mechanically from Gate A; Gate A and Gate E are always rerun on the exact final candidate immediately before Git initialization.

### Spec coverage

- Arbitrary domains / unlimited projects: Task 2.
- Empty application state: Task 9 Gate C.
- Remove four-project/fixed-domain contract: Task 2.
- Codex orchestration removal: Task 3.
- Agent provenance: Task 4.
- Backend-neutral executions: Task 5.
- Optional generic Slurm adapter / institution-specific scheduler removal: Task 6.
- Empty/generic UI and retained planning/review/risk primitives: Task 7.
- Synthetic examples/public docs/package hygiene/CI: Task 8.
- Privacy and portability: Tasks 1, 8, 9, 10.
- Fresh history: Tasks 1-9 prohibit Git; Task 10 owns initialization.
- GitHub publication and v0.1.0 release: Task 10.
- No backward compatibility/migrations/provider frameworks: global constraints and deletion tasks.

### Type/interface consistency

- `ExecutionInput.execution_id` is the stable internal ID.
- `ExecutionInput.backend + external_id` uniquely identifies the external execution.
- Slurm stores `backend="slurm"` and numeric job ID in `external_id`.
- `ExecutionObservationInput.execution_id` links observations to the generic execution.
- Exact observation replay is idempotent; new observations update current summary in ingestion order.
- Slurm SSH target remains command-time adapter input and is not persisted in generic execution state.
- `SemanticEventInput.source_agent` and `source_session` are optional strings.
- Generic project `domain` is a plain non-empty string.

### Lower-capability executor rule

When an instruction and existing code appear inconsistent, do not invent a compatibility shim. Prefer this plan's explicit final public contract, delete obsolete private behavior, keep the package runnable at each task boundary, and use the named focused test slice to prove the boundary before continuing.
