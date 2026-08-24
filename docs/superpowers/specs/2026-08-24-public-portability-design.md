# Research Dashboard public portability design

**Status:** accepted public design contract
**Date:** 2026-08-24
**Target repository:** Allison0802/research-dashboard
**Initial public version:** 0.1.0
**License:** MIT

## Product and supported environment

Research Dashboard is a developer-oriented framework for visualizing evidence-backed, agent-assisted research work. It supplies a reusable domain model, reference dashboard, explicit semantic-event ingestion, evidence-backed derived state, planning and review primitives, optional external-execution integrations, and adaptable examples.

It is not a complete, unmodified application for arbitrary end users. Adopters are expected to fork and customize ordinary Python, templates, CSS, and data-model code. Version 0.1 does not provide a plugin SDK, provider framework, widget framework, theme system, or general configuration platform.

Public v0.1 supports macOS and Linux with Python 3.11 or later. Windows support is not required. Developer instructions use python3. The conventional path is:

~~~
git clone https://github.com/Allison0802/research-dashboard.git
cd research-dashboard

python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

.venv/bin/research-dashboard init
.venv/bin/research-dashboard serve
~~~

The release does not require a one-click installer, daemon, Codex, Slurm, a cloud service, or PyPI publication.

## Core architecture and project model

The reusable conceptual spine is:

~~~
Project
  ↓
Workstream / Task
  ↓
Semantic Event + Evidence
  ↓
Derived State
  ↓
Risk / Review
  ↓
Reference Dashboard
~~~

Planning remains deliberately separate from evidence-backed execution:

~~~
Governing Plan
  ↓
Roadmap
  ↓
TODO
~~~

A plan and a TODO describe intended work; neither proves execution occurred. Semantic events remain the authority for evidence-backed execution state.

The application supports zero, one, or any number of projects. A project has a stable ID, name, arbitrary non-empty domain string, lifecycle, optional context path, optional update horizon, and zero or more explicitly registered roots. Domain is not an enum, and there is no distinguished portfolio membership.

The fixed private portfolio model is obsolete. Remove rather than generalize:

- exact project-count validation and fixed expected IDs;
- fixed roots, context files, governing-plan files, exclusions, scheduler truth, and manifests;
- matching CLI validation paths and tests; and
- fixed domain-specific portfolio queries, their aliases, dispatchers, and tests.

Do not add a compatibility version of the old portfolio model.

## Empty database contract

research-dashboard init creates the complete current schema without a sample portfolio, automatic first project, hidden scheduler configuration, or agent configuration.

An empty database has zero user and research records. Exactly the initialized review_state singleton is allowed. The installed-release test verifies that precise allowance while checking all other application-record expectations for an empty state.

Synthetic examples live outside production state and are never imported automatically. Generic project registration is sufficient.

## Agent-neutral integration

Core does not orchestrate agents. Agents run elsewhere and communicate material changes through validated semantic-event JSON or the Python API:

~~~
external agent
    ↓
semantic-event JSON / Python API
    ↓
validation
    ↓
append-only event + evidence ledger
    ↓
derived state
    ↓
dashboard
~~~

The supported core write interfaces are the Python API and CLI JSON ingestion. There is no HTTP mutation API in v0.1.

Semantic events may include optional source_agent and source_session strings. They support provenance visualization, including latest material updates and activity grouping, but do not create an agent registry or agent-run-management subsystem.

Remove public-core machinery and dashboard actions whose purpose is to launch, recover, clean up, or manage Codex processes; install Codex hooks or skills; change Codex configuration; or assume a Codex executable. Do not expose generic placeholder agent-launch buttons. Research Dashboard observes and visualizes agent work; it is not an agent orchestrator.

A small, entirely synthetic examples/codex/ example may show an external local workflow producing a valid event payload, submitting it with research-dashboard event add, and observing the dashboard update. It must not contain private hooks, paths, per-user installations, process-management infrastructure, or a Codex installation assumption. Codex is not a packaged runtime dependency.

## Generic execution and Slurm

Replace specialized persistence with executions and execution_observations. An execution may optionally belong to a project, workstream, or task; it records identity and normalized current state. Observations are append-only history.

The normalized states are:

~~~
SUBMITTED
QUEUED
RUNNING
COMPLETED
FAILED
CANCELLED
UNKNOWN
~~~

Generic execution state does not require scheduler-specific identifiers, and old specialized tables are deleted with no private-database migration path.

Exact observation replay is idempotent: an exact duplicate returns the existing observation without changing the summary. A newly accepted observation updates the current execution summary according to ingestion order, so the current summary follows the most recently accepted observation.

Slurm is an optional, explicitly namespaced adapter, for example under src/research_dashboard/adapters/slurm.py and research-dashboard slurm. It translates explicitly registered Slurm job observations into generic execution and semantic state where appropriate. Delete generic-looking commands whose actual meaning is scheduler-specific, including the legacy cluster, job, and poll-hpc commands.

Core initialization and serving work without scheduler commands, SSH, or a registered cluster. The generic execution table does not persist an SSH target or cluster-registry state. Adapter command inputs are validated as a numeric job ID and one safe host or user-at-host token; tests use mocks and deterministic fixtures only, never a real remote system.

Public Slurm support contains no institution-specific banners, help links, email addresses, login-message allowlists, historical job IDs, remote targets, or organization-specific assumptions. Generic SSH output handling, if necessary, must be based on observed generic command behavior.

## Planning, reviews, and deletion

Retain generic governing plans, conservative roadmap bootstrap, roadmap items, TODOs, and plan synchronization. Plan parsing is deterministic and conservative: ambiguous prose is not silently turned into structured tasks, and plan parsing is not an AI subsystem. Planning is optional; a project may emit events without a governing plan.

Remove specialized investigation and correction-request machinery that exists primarily for the obsolete workflow. Retain the general primitives: events, evidence, risks, reviews, and planning. Future adopters can build specialized workflows from those primitives without preserving obsolete paths.

## Runtime paths and configuration

Default runtime state is ~/.research-dashboard/. RESEARCH_DASHBOARD_HOME is the sole production runtime-location override. Runtime state derives from Path.home() by default or from that override; repository-relative resources and explicit user inputs do not add another runtime-location override. Production source must not contain a fixed, user-specific home-directory prefix. Tests may use temporary absolute paths.

Do not introduce a version-0.1 TOML configuration layer. Use the SQLite registry for project and work data, the runtime-state directory above with its sole override, and explicit CLI arguments where appropriate. Avoid layered configuration machinery.

## Reference UI, examples, and documentation

Retain the server-rendered FastAPI/Jinja approach. The UI is generic with respect to project count, domain, and agent source; it is safe for an empty database and provides a useful empty state explaining the conceptual flow and how to add data. Do not undertake a wholesale redesign or introduce dashboard widget registries, runtime-layout configuration, or a theme framework.

Create an examples/ tree containing only synthetic material: a basic project and event example, the optional Codex example above, and a Slurm README. Examples demonstrate contracts and are never imported automatically.

Public documentation explains the reusable core rather than one person's workflow. Required documentation includes the README, architecture, event-contract, extending guidance, and this specification under docs/superpowers/specs/.

## Privacy and candidate-tree standard

Before any history exists, perform a full private-information audit. The candidate has zero tolerance for personal names or usernames; fixed home-directory paths; personal or inherited institutional email addresses; cloud-storage roots; private project titles, IDs, context paths, or plan paths; private hostnames and scheduler targets; historical scheduler IDs; authentication material; internal URLs; machine-specific integrations or symlink targets; databases; runtime state; backups; exports; logs; hidden agent state; credentials; cookies; tokens; or API keys.

The one-time exhaustive denylist is external to the candidate tree. Do not commit a document enumerating removed sensitive strings. Permanent repository tests instead enforce generic invariants such as no fixed user-home paths, no obvious secrets, no private production database, no runtime logs, and no unexpected generated artifacts.

## Fresh history, gates, CI, and release

The candidate intentionally has no Git history. Preserve that condition through all refactor tasks: do not initialize a repository, import historical commits, copy metadata from another repository, stage files, or create commits. The first history must represent the sanitized public system.

Before initialization, all of these local gates must pass:

1. The complete automated suite passes.
2. A distributable wheel builds using only candidate-tree inputs.
3. The built wheel installs in a new isolated environment.
4. Installed init creates the precise empty database described above.
5. The installed dashboard renders with zero projects and zero events.
6. A synthetic project-to-event-to-derived-state-to-UI flow succeeds.
7. Core operation succeeds without Codex available.
8. Core operation succeeds without Slurm commands available.
9. Deterministic mocked Slurm-adapter tests pass with no real remote query.
10. A portability scan finds no forbidden absolute or user-specific assumptions.
11. The external private-information and secret scan has no findings.
12. A candidate-tree audit finds no database, runtime state, log, virtual environment, cache, credential, backup, private export, or unexpected symlink.

Only after all gates pass may the first Git initialization occur. Create an appropriate ignore file before staging, review the complete staged candidate, and make the initial public-release-candidate commit. Do not tag it yet.

The publication destination is https://github.com/Allison0802/research-dashboard. Publish only the clean main branch, with no private historical branch.

The initial public commit includes CI for Ubuntu and macOS using Python 3.11. CI installs development dependencies, runs the public suite and portability scan, and builds the wheel. It does not require Codex, Slurm, SSH credentials, private environment variables, or private fixtures. At implementation time, verify every selected GitHub Action version against its official documentation; this specification deliberately does not freeze Action major versions.

The required release sequence is:

~~~
refactor
↓
local tests
↓
wheel build
↓
clean wheel install
↓
empty-state smoke
↓
synthetic end-to-end smoke
↓
Codex-absent verification
↓
Slurm-absent verification
↓
mocked Slurm verification
↓
portability scan
↓
private-information scan
↓
candidate-tree audit
↓
first Git initialization
↓
staged-tree review
↓
first clean commit
↓
create Allison0802/research-dashboard
↓
push main
↓
CI passes on Ubuntu and macOS
↓
tag v0.1.0
↓
create the v0.1.0 release
~~~

A remote CI failure blocks tagging and release. Correct ordinary defects on main, obtain green CI, then tag the accepted commit; a normal failure does not require history rewriting.

## Non-goals, package direction, and success criterion

Public v0.1 does not provide backward compatibility with the private system, database migration, a fixed portfolio, an agent-orchestration platform, an agent-provider or scheduler-provider SDK, non-Slurm schedulers, Windows support guarantees, HTTP writes, authentication, a cloud backend, multi-user hosting, a plugin framework, configurable widgets, a theme platform, one-click installation, daemon installation, automatic project discovery, automatic example data, AI plan parsing, or PyPI publication.

The intended responsibility split includes domain, database, registry, events, state, planning, plan synchronization, risks, reviews, provenance, CLI, web, templates, static assets, schema, and an explicitly namespaced Slurm adapter. Delete obsolete files or reduce mixed-responsibility files only after inspecting their actual role; do not remove unrelated capabilities merely because they share a file with obsolete behavior.

Deletion is preferred to compatibility. Do not retain obsolete functions, aliases, CLI commands, schemas, manifests, migrations, flags, or wrappers only to preserve prior behavior.

Public v0.1 succeeds when a developer with no knowledge of the original portfolio, no private projects, no Codex installation, and no Slurm installation can clone the repository, install it, initialize an empty database, start the dashboard, add an arbitrary synthetic project, ingest an agent-produced semantic event, and see the resulting work state visualized.
