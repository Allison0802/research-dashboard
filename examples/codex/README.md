# External event producer example

This directory demonstrates the only integration boundary needed for an
external event producer: create a valid semantic-event JSON file and submit it
through the public CLI. It is documentation only; it does not import, run, or
configure any external tool automatically.

After registering the synthetic project, submit the supplied synthetic payload:

```bash
.venv/bin/research-dashboard event add --input examples/codex/example-event.json
```

The `source_agent` and `source_session` values are optional provenance labels.
They do not create an agent registry or grant the dashboard authority to start
or manage the external producer.
