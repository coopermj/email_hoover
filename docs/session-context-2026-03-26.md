# Session Context: 2026-03-26

## Product state

The operator console is now discovery-first and tabbed:

- `Candidates` is the default tab.
- `Rules`, `Activity`, and `Exceptions` are separate tabs.
- The right-side editor appears only on `Candidates` and `Rules`.

## Behavior added or corrected

- Gmail OAuth flow remains wired through the dashboard.
- Newsletter discovery is available from the `Scan Gmail For Newsletters` action.
- `0` stale days is allowed and means immediate eligibility for matching sender mail.
- Recent cleanup activity now renders sender and subject for completed/planned actions when that snapshot is available.
- Once a sender has a cleanup rule, that sender is excluded from `Candidates`.
- Manual rule creation also reconciles matching pending candidates to `approved`.
- Dashboard actions preserve tab context instead of always redirecting back to the default view.

## Main files touched

- `app/web/routes.py`
- `app/services/rules.py`
- `app/services/executor.py`
- `app/templates/dashboard.html`
- `app/templates/base.html`
- `app/templates/partials/candidate_list.html`
- `app/templates/partials/rule_workspace.html`
- `app/templates/partials/rule_editor.html`
- `tests/test_dashboard_routes.py`
- `tests/test_rule_repository.py`
- `tests/test_executor.py`

## Verification

- `python3 -m pytest -q`
  - Result: `86 passed`

## Live runtime state at handoff

- Local app is running on `http://127.0.0.1:8765`
- Current branch: `main`
- Pending changes were ready to commit at the time this note was written
