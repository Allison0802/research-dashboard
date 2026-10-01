# Project admission and synchronization

Version 0.3.0 adds explicit portfolio membership and optional source imports.
The original public design remains agent-neutral: arbitrary domains, portable
runtime paths, an empty initial database, CLI/Python writes, and GET-only views.
No private project registry, agent hook, daemon, or scheduler is installed.

## Admit independent ongoing projects

A new project needs an independent current study/manuscript, its own local
context or plan, and a concrete reason for tracking it. A folder or old output
alone does not qualify. The caller establishes current research meaning; the
CLI validates the specific marked root, domain, reason, and lifecycle.

```bash
.venv/bin/research-dashboard project admit \
  --path "$PWD/path/to/your-project" \
  --domain "Climate Science" \
  --name "Example Climate Model" \
  --reason "Independent current study with its own context and plan"
```

Use the returned project ID and roots for subsequent events. Exact existing
roots retain their IDs. Newly discovered roots are hidden Needs classification
candidates. Existing explicitly registered Active, Waiting, and Paused projects
remain admitted, including projects with no registered roots. Retired or
Completed projects cannot be automatically reactivated.

## Synchronize known projects

```bash
.venv/bin/research-dashboard sync --dry-run
.venv/bin/research-dashboard sync
```

Normal sync reads only the registry and named logs at admitted roots. It never
scans all local folders. To explicitly discover candidates within one workspace:

```bash
.venv/bin/research-dashboard sync --root "$PWD/workspace" --domain "Climate Science" --dry-run
```

Remove `--dry-run` to register the marked roots as hidden candidates, then admit
only independent current projects. The product installs no background daemon;
your own scheduler can run normal sync at an appropriate interval.

## Dated project logs

Sync reads `PROJECT_LOG.md`, `project_reports/PROJECT_LOG.md`, `CHANGES.md`, and
`CHANGELOG.md`. Entries use `## YYYY-MM-DD — title`. Optional `### Current step`,
`### Purpose`, and `### Next step` sections become a concise progress summary.
Multiline qualifications remain intact. The original source text and date are
preserved, and unchanged entries are not imported twice. Same-day entries are
grouped without guessing within-day order. The display shows a source date;
UTC midnight is only a storage-ordering convention. Importing a log does not
independently validate its analytical claims.

## Incoming activity

Use `inbox add --title ... --note ...` for incoming records. Exact project IDs,
qualified `Project: NAME` references, registered roots, and supported record
references can identify a unique owner. The CLI immediately attempts to route a
new item; normal sync also reconciles pending items. A nested root wins for an
actual file beneath it; separate project paths or conflicting references remain
ambiguous. A historical candidate list does not override current ownership.

`inbox route --dry-run` previews the result; `inbox list` shows unresolved
activity. Attributable records become append-only project events with original
payload and date. They create no TODO or task transition, and their scientific
evidence stays unknown. Cross-project or unresolved references stay pending.

## Terminal tools

Any authorized local-terminal tool can submit event JSON using
`.venv/bin/research-dashboard event add --input EVENT_FILE`. Remote Desktop
Commander uses the same path; no Codex-specific connector is required.
Inspect the acceptance receipt and its conflict/state fields before reporting
synchronization. Work must leave an event or a project log to appear here; sync
does not scrape unrelated webpage conversations.
