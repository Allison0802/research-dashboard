# Extending Research Dashboard

Research Dashboard is ordinary Python application code intended to be forked
and adapted. Keep the event ledger and its evidence boundary intact while
making domain-specific changes in the places that own each concern.

| Need | Where to change it |
|---|---|
| Domain semantics and validation | `src/research_dashboard/domain.py`, plus focused tests in `tests/`. |
| External event producers | Produce `SemanticEventInput` JSON and submit it through `research-dashboard event add`, or call the Python event API. |
| Dashboard presentation | Jinja templates in `src/research_dashboard/templates/` and CSS or JavaScript in `src/research_dashboard/static/`. |
| Derived state and queries | Read-model code in `src/research_dashboard/state.py` and the related CLI/web callers. |
| Optional external adapters | A narrowly scoped module under `src/research_dashboard/adapters/`, loaded only by the corresponding explicit command path. |

Start by adding a failing test for the desired behavior. Keep browser routes
read-only: external systems and CLI/API callers supply events, while the web
application presents derived state.

There is no plugin SDK in version 0.1. An adopter that needs a new domain rule,
adapter, template, or query should make the change in their fork, document its
contract, and add tests rather than relying on undisclosed runtime discovery.
