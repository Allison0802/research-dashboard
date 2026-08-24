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
