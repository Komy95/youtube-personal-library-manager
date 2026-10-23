# Public release roadmap

Updated 2026-10-24. Repository remains private. Source license: [MIT](../LICENSE).
[Plan](OPEN_SOURCE_PLAN.md) · [Project roadmap](../ROADMAP.md) ·
[Distribution audit](DISTRIBUTION_AUDIT.md)

Scope clarification (2026-10-24): this is a small community repository shared
as-is. CI, independent-user/agent acceptance and additional platform testing
below are optional improvements, not required maintainer commitments or gates
for sharing the current code. They remain unchecked because they are unverified.
Publication still needs owner approval and accurate privacy/license/scope claims.

## M0 — Reusable source baseline

- [x] Working Python gateway and synthetic offline tests in Git.
- [x] Local-first per-user OAuth/storage model; no model-server requirement.
- [x] Distinguish verified offline behavior from live and platform acceptance.

## M1 — Distribution hygiene and license

- [x] Remove personal operations, inherited account approvals and outdated totals
  from current docs; remove unrelated provider-switch scripts.
- [x] Add selected MIT license and package metadata; review direct dependency
  license metadata and retain redistribution guidance.
- [x] Add security reporting and contribution/support guidance.
- [x] Strengthen ignore rules and separately inspect intended tracked paths.
- [x] Scan original reachable history with Gitleaks 8.30.1; no secret findings.
- [x] Scan the candidate tree/artifact and fresh remote clone/history with no
  secret findings; verify MIT metadata and private replacement/archive identities.

## M2 — Human onboarding

- [x] Short [setup](SETUP.md), [OAuth](GOOGLE_OAUTH.md), [editing](EDITING.md)
  and [troubleshooting](TROUBLESHOOTING.md) guides.
- [x] Per-user credentials, external data paths, account checks, token expiry,
  quotas and separate write approval documented.
- [x] Fresh Windows/Python 3.12.10 editable installation and dependency check.
- [ ] Independent clean-machine/new-user read-only OAuth acceptance.
- [ ] Verify macOS/Linux installation/permissions before advertising support.

## M3 — Agent and editing workflow

- [x] Detailed [agent guide](AGENT_SETUP.md), copyable starter prompt and generic
  root agent rules with contributor tooling kept separate.
- [x] [Synthetic walkthrough](EXAMPLE_WORKFLOW.md) and example assignments/output.
- [x] Backup, exact plan hash, local dry run, remote preflight, quota allowance,
  post-write checks and journal-preserving recovery instructions.
- [x] State unsupported operations and privacy/evidence boundaries explicitly.
- [ ] Fresh independent agent walkthrough with consent/login/approval stops.

## M4 — Packaging and credential-free CI

- [x] Validate a wheel installation and license files/metadata in built artifacts.
- [ ] Choose supported Python/OS matrix and add offline CI.
- [ ] Add maintained dependency/secret checks to CI without live account fixtures.
- [ ] Confirm required checks against the exact release candidate.

## M5 — Optional community acceptance

- [ ] Decide initial scope: read-only, or clearly labeled advanced writes.
- [ ] For advanced writes, complete separately approved disposable-playlist
  acceptance and scoped post-write/recovery verification.
- [ ] Publish accurate capability/verification table and known limits.

## M6 — Publication

- [x] Repeat history/privacy/license review of the clean distribution; no private
  personal-record or secret findings. Public ownership/noreply attribution remains.
- [ ] Owner approval to publish the exact candidate as a small, as-is project.
- [ ] Change visibility only after owner approval; verify public state.
- [ ] Tag reviewed version and publish setup/scope/recovery release notes.

## Current evidence and limits

2026-10-24: fresh Windows/Python 3.12.10 editable install, CLI help, `pip check`
and full offline suite after cleanup: **276 passed, 2 skipped in 7.74s**. Skips concern POSIX
permissions and unavailable symlinks. Documentation command/options, PowerShell
syntax and relative links were checked; the synthetic walkthrough is covered.

No independent clean-machine OAuth, new-user account mutation, fresh independent
agent session or macOS/Linux acceptance is claimed. No account-specific task or
approval is part of this roadmap. GUI, whole-playlist deletion, general
rename/reorder controls and scheduled writes remain deferred.
