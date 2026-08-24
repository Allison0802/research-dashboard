# Semantic event contract

`SemanticEventInput` is the validated write contract for Research Dashboard.
Submit one JSON object through `research-dashboard event add --input FILE` or
the Python API. Unknown fields are rejected, and string fields are trimmed
before validation.

## Event fields

| Field | Required | Meaning |
|---|---|---|
| `event_id` | Yes | UUID that identifies this immutable event. Replaying an exact event is idempotent. |
| `project_id` | Yes | Non-empty ID of a previously registered project. |
| `workstream` | No | Optional non-empty grouping within a project. |
| `task_key` | No | Optional non-empty stable task identifier. |
| `event_type` | Yes | Non-empty event label. A `state_change` requires `new_state`; a `risk` requires `risk_type`. |
| `previous_state` | No | State observed before the change, when known. |
| `new_state` | No | State observed after the change. Required for `state_change`. |
| `importance` | Yes | Non-empty caller-defined significance label. |
| `risk_type` | No | `Research` or `Operational`; required for `risk` events. |
| `risk_severity` | No | Optional non-empty caller-defined severity label for a risk. |
| `epistemic_status` | Yes | One of `Observed`, `Derived`, or `Inferred`, describing how the claim was obtained. |
| `context` | Yes | Non-empty factual context needed to interpret the update. |
| `what_changed` | Yes | Non-empty description of the material change. |
| `cause` | No | Optional known cause; omit it when unknown. |
| `impact` | No | Optional expected or observed consequence. |
| `next_action` | No | Optional proposed follow-up; it is not evidence of completion. |
| `confidence` | No | `High`, `Medium`, or `Low` confidence in the event statement. |
| `governing_plan_path` | No | Optional caller-supplied reference to a governing plan. |
| `source_agent` | No | Optional non-empty producer label for provenance. It does not register or control an agent. |
| `source_session` | No | Optional non-empty producer-session label for provenance. |
| `observed_at` | Yes | Time at which the event was observed; it must include a UTC offset. |
| `ingested_at` | No | Time accepted by the dashboard; defaults to the current UTC time. |
| `corrects_event_id` | No | UUID of an earlier event this event corrects. The earlier event remains in the ledger. |
| `evidence` | No | List of evidence records supporting the event; defaults to an empty list. |

## Evidence records

Each item in `evidence` has these fields:

| Field | Required | Meaning |
|---|---|---|
| `evidence_type` | Yes | Non-empty category chosen by the producer, such as `repository` or `report`. |
| `locator` | Yes | Non-empty stable pointer to the evidence. It may be a repository-relative path, an external identifier, or another resolvable reference. |
| `authority` | Yes | Non-negative integer expressing the producer's authority level for this evidence. The dashboard stores it; it does not infer trust. |
| `observed_at` | No | Time the evidence itself was observed, with a UTC offset when supplied. |
| `availability` | No | `available`, `unavailable`, or `unknown`; defaults to `available`. |

Evidence is provenance, not a side channel for data import. Provide only
locators that the producer is permitted to disclose. An unavailable locator can
still document what was observed, but should be labelled accordingly.

## Timezones and provenance

`observed_at`, `ingested_at`, and an evidence `observed_at` accept only
timezone-aware ISO-8601 datetimes. Use an explicit offset, preferably `+00:00`,
so a reader can order observations unambiguously across systems.

Use `source_agent` and `source_session` only to identify the producer context
that supplied the event. They are optional metadata, not an identity system,
execution controller, or guarantee that a claim is true. Pair material claims
with evidence and choose `Observed`, `Derived`, or `Inferred` accurately.
