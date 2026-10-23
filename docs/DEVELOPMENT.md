# Contributor workflow

These rules govern source changes, not installing/using the application.

## Existing project roles

- Codex handles architecture, planning, review, integration and verification.
- DeepSeek authors implementation, tests and configuration in small bounded
  tasks, through the configured bridge or
  `opencode run --pure -m deepseek/deepseek-v4-pro --variant low`.
- Delegate early; a review after complete self-implementation is not offloading.
  Supply minimum sanitized interfaces/source and acceptance cases, never account
  records, collection contents, credentials or secrets. Do not send whole private
  files where a bounded interface is sufficient. Do not bypass approval review.
- Review generated work, retain CI/security gates, verify locally and report
  actual contributions/usage without claiming unmeasured savings.

Documentation/planning changes do not require a runtime worker. End users do not
need these tools or roles merely to operate the Python gateway.

## Exploration and changes

Prefer CodeGraph for structural questions; use `codegraph_explore` for focused
symbol/source context. Use `rg` for literal text. Do not delegate repeated
exploration or re-verify fresh structural results with grep. If an index is
absent, ask before `codegraph init -i`; when explicitly requested, initialize it.
Use `codegraph sync` to refresh an existing index. Never include `.codegraph/` in Git.

Preserve account binding, exact scopes, external credential paths, complete
transactional snapshots, history/duplicates/order, hash-bound plans, private new
destinations, preflight, journal-before-write and at-most-once attempts. Test
offline using synthetic data. Live writes need a separately approved exact plan.

Architecture: `config/models` -> `auth/api` -> `db/sync` -> `content/analysis`
-> `planning` -> `migration/write_auth` -> `cli/workflow_cli`.
Schema version is 2. `analysis` validates/imports evidence-grounded JSON; it has
no model-server transport. Consult source/CLI help before changing interfaces.

## Checks and records

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q --tb=short -rs
git diff --check
```

Use normal external temporary storage for auth tests; never move those fixtures
inside the checkout or remove path guards to make sandbox tests pass. Platform
skips are not evidence of passing those platforms. Run checks appropriate to the
change; docs-only edits need command/link verification, not new implementation tests.

Update `ROADMAP.md` after every work package and the public-release checklist
when relevant. Preserve dated historical evidence; mark only verified work done.
Keep personal operation logs outside distributable files and Git history.
Never inherit private-data consent or account
mutation approval from a historical document.
