# Reporting progress from a terminal agent

This synthetic example requires no specific agent runtime. Any authorized tool
with local files/terminal access, including Remote Desktop Commander, can use
the installed CLI.

Read the project's own instructions. Run `.venv/bin/research-dashboard sync
--dry-run` for admitted project IDs and roots. Use `project admit` only for an
independent current project with its own context/plan and a concrete reason.

After the basic example's explicit registration, submit its event:

```bash
.venv/bin/research-dashboard event add --input examples/basic/event.json
```

Include `source_agent`, `source_session`, and real evidence locators when known.
Use `progress.current_step`, `progress.purpose`, and `progress.next_step` for the
reader-facing summary. Keep diagnostics in full context; do not include raw
participant data or credentials. For an existing task, preserve correct state
transitions. For a standalone session summary, use a distinct session task key
and omit `new_state`.

Verify the final `accepted`, `event_id`, `sequence`, `status`, `current_state`,
and `conflict` receipt fields. Exact UUID/payload replay is idempotent; ingestion
does not establish scientific acceptance.

If only file writes are available, append a dated project log:

```md
## 2026-10-01 — Step 2: model checks

### Current step
Step 2: estimates are generated; validation remains pending.

### Purpose
Check whether outputs follow the approved analysis design.

### Next step
Validate before drafting manuscript tables.
```

Sync imports logs at admitted roots while retaining dates and qualifications.
It does not read arbitrary chats. See [project sync](../../docs/project-sync.md).
