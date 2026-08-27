# Research Dashboard architecture

Research Dashboard is a local, evidence-aware framework for presenting the
state of arbitrary research projects. It stores a registry, append-only
semantic events, evidence records, planning data, review checkpoints, and
optional execution summaries in SQLite.

## System shape

```text
project registration
        ↓
external event producer → CLI or Python caller → canonical event writer
                                                   ↓
                              validation → writable SQLite preflight → append-only ledger
                                                                         ↓
                                                              deterministic read models
                                                                         ↓
                                                               GET-only dashboard views
```

External producers retain write responsibility. The dashboard accepts their
validated events through the CLI or Python API and never mutates data through
browser routes.

## Runtime boundary

The checkout contains source, tests, and documentation. Runtime state is kept
separately in the directory selected by `RESEARCH_DASHBOARD_HOME` (or the
portable `~/.research-dashboard` default supplied by the application). Runtime databases, logs,
backups, and environments are not source artifacts and are not committed.

## Core records and authority boundaries

The system keeps the following concerns separate:

1. **Registry** records explicitly added projects, optional project roots, and
   governing-plan references. A project domain is arbitrary text; the registry
   never maps it to a fixed portfolio or behavior.
2. **Semantic event ledger** records observed project changes and their
   evidence. Events are append-only; later events can correct earlier state
   without rewriting history.
3. **Read models** derive current project, portfolio, risk, review, and
   evidence views deterministically from the registry and event ledger.
4. **Manual planning** holds explicit roadmap and TODO items separately from
   observed semantic state. It cannot change an event-derived status.
5. **Execution store** records generic backend, external reference, and
   normalized state. Optional adapters translate an explicitly requested
   external backend into this neutral store; the core and web views remain
   backend-neutral.

## Semantic update path

An authorized caller submits a semantic-event payload through the canonical
writer. The writer validates the payload, obtains the configured local
database through `load_settings()`, verifies SQLite write availability with a
short write transaction, and then delegates append-only persistence to the
event ledger. It returns a six-field acceptance receipt: `accepted`,
`event_id`, `sequence`, `status`, `current_state`, and `conflict`. An exact
event replay is idempotent and returns the same receipt.

The CLI passes the receipt through unchanged. Database errors are structured as
either a transient busy condition, a non-writable database, or a non-transient
generic database error; callers decide whether to retry. The browser
presentation layer has GET routes only; mutation authority remains outside
HTTP.

Optional event provenance is recorded as source agent and session metadata.
It is displayed only when the event actually carries it. The full field and
evidence contract is documented in [event-contract.md](event-contract.md).

## Optional adapters

The Slurm adapter is an optional, explicitly named integration. Its command
path is loaded lazily, so ordinary initialization, event ingestion, and web
serving do not require scheduler software or a remote connection. Its normalized
execution records stay separate from the semantic-event ledger.

## Planning behavior

Planning is intentionally optional and conservative. A governing plan may
initialize an empty roadmap only from explicit task headings or top-level
checklist rows. Prose-only or ambiguous plans stay unchanged for explicit
review; no AI parser or inferred roadmap mutation is used.

## Current-state presentation

The homepage remains usable when the registry is empty and explains how to
begin with a project and semantic event. For registered projects it presents
derived briefs before portfolio queues. Project detail preserves the audit
trail, planning display, and generic execution state without treating planned
work or backend activity as scientific acceptance.

## Non-goals

Research Dashboard does not provide automatic project discovery, fixed domain
workflows, HTTP writes, a provider or plugin framework, AI plan parsing,
machine-specific integration, runtime-state publication, a cloud backend, or
automatic scheduler-account scans. It has no plugin SDK; adopters extend a
fork through ordinary Python, templates, CSS, and tests as described in
[extending.md](extending.md).
