# Research Dashboard

Research Dashboard is a local framework for visualizing evidence-backed
research work. It stores projects, plans, append-only semantic events,
evidence, derived state, risks, and reviews in a local SQLite database.

## Development installation

Use Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/research-dashboard init
.venv/bin/research-dashboard serve
```

Runtime state defaults to `~/.research-dashboard/`. Set
`RESEARCH_DASHBOARD_HOME` to use a different runtime location.

### Event writer receipts

`research-dashboard event add` uses the canonical local writer. It validates
the JSON event, checks that SQLite can accept a write, appends the event and
its evidence, then prints one acceptance receipt with exactly `accepted`,
`event_id`, `sequence`, `status`, `current_state`, and `conflict`. Replaying
the exact same event ID and payload returns the original receipt without
creating a second event.

If SQLite cannot accept the write, the command exits with status 2 and writes
a JSON error envelope to standard error. The envelope distinguishes a
transient busy database from a non-writable database so callers can make an
explicit retry decision.

## Getting started

Add an arbitrary project explicitly, then submit a validated event:

```bash
.venv/bin/research-dashboard project add \
  --project-id example-climate-model \
  --name "Example Climate Model" \
  --domain "Climate Science" \
  --lifecycle Active \
  --update-horizon-minutes 10080

.venv/bin/research-dashboard event add --input examples/basic/event.json
```

The files under `examples/` are synthetic documentation fixtures. They are
never imported automatically.

Use `.venv/bin/research-dashboard project root-add` to register a project root,
`.venv/bin/research-dashboard project plan-set` to register a governing plan,
and `.venv/bin/research-dashboard portfolio query` to query derived state.

The browser interface serves GET routes only. Write events through the CLI or
the Python API, then use the dashboard to inspect the resulting state.

## Documentation

- [Architecture](docs/architecture.md)
- [Semantic event contract](docs/event-contract.md)
- [Extension guide](docs/extending.md)
- [Public design contract](docs/superpowers/specs/2026-08-24-public-portability-design.md)
