# YouTube Personal Library Manager — Roadmap

Updated 2026-10-24. This roadmap tracks distributable product capabilities and
verification. It contains no account-specific operational tasks or permissions.
[Release checklist](docs/PUBLIC_RELEASE_ROADMAP.md) ·
[Release plan](docs/OPEN_SOURCE_PLAN.md) · [Audit](docs/DISTRIBUTION_AUDIT.md)

## Project overview

A local Python 3.12+ CLI for owned YouTube playlists. Each user supplies their own
Google project, OAuth client, quota and external SQLite data directory. No hosted
service or model-server integration is included. Source is MIT licensed.

## Implemented capabilities

- [x] Read-only Desktop OAuth, separate write OAuth and external token storage.
- [x] Complete transactional inventory snapshots with account binding, history,
  ordering, duplicate memberships and soft deletion after successful sync only.
- [x] Offline playlist/video inspection and JSON export.
- [x] User-supplied text/subtitle evidence and validated analysis JSON import.
- [x] Evidence-grounded facts, proposed categories and missing-content handling.
- [x] Explicit assignment plans, full-hash review, existing owned destinations
  and duplicate reports; optional within-playlist duplicate removal.
- [x] Separate plan approval, live preflight, journal-before-write and at-most-once
  non-idempotent attempts, with failed/uncertain replay blocked.
- [x] Short human setup/OAuth/editing/troubleshooting guides, detailed agent
  workflow and credential-free synthetic walkthrough.

Implementation is not universal live acceptance. The CLI exposes mapping-based
creation/addition and optional duplicate removal, not general rename/reorder or
whole-playlist deletion workflows. Arbitrary saved lists, Watch Later, history,
video editing and third-party transcript downloads are outside scope.

## Verified baseline

- [x] Fresh Windows/Python 3.12.10 environment: editable installation, CLI help
  and dependency consistency succeeded (2026-10-24).
- [x] Full offline suite after cleanup: **276 passed, 2 skipped in 7.74s** (2026-10-24).
  Skips concern POSIX permissions and unavailable symlinks.
- [x] Documentation command paths/options, PowerShell syntax and relative links
  checked; the existing synthetic end-to-end workflow passes without account access.

These checks establish local offline behavior. They do not establish a clean
machine, a new user's OAuth, an independent agent session, macOS/Linux behavior
or acceptance of every live operation. No user's account approval transfers.

## Distribution cleanup — 2026-10-24

- [x] Remove personal migration logs, previous owner approvals, usage/cost logs
  and outdated status totals from distributable documentation.
- [x] Keep generic user/agent rules and current evidence separate from acceptance
  still required for a release. No saved plan or consent is shipped.
- [x] Remove unrelated provider-switching utilities from the product file set.
- [x] Add the owner-selected MIT license, package license metadata, dependency
  license notes, security reporting and contribution/support guidance.
- [x] Review ignore rules for write tokens, OAuth downloads, account directories,
  logs and local backups; review the actual tracked manifest separately.
- [x] Audit original reachable history locally with Gitleaks 8.30.1: no secret
  findings. Preserve an offline recovery bundle outside publication refs.
- [x] Exact staged tree and wheel scans: no secret findings. Built wheel declares
  MIT and includes LICENSE; installation, imports outside the checkout, CLI help
  and dependency consistency passed. Runtime source/tests are unchanged.
- [x] Verify sanitized remote distribution: a fresh clone contained only the
  clean root history, and both history/file scans found no secrets. The replacement
  has a distinct GitHub identity, MIT detection and private visibility; the
  original remains a separately named private, read-only archive. A pre-cleanup
  commit returns HTTP 404 in the replacement. See the audit report.

## Community scope — 2026-10-24

This is a small, as-is project for interested users, not a broadly certified
product. README now leads with useful workflows and human/agent entry points.
The maintainer is not committing to testing every platform or user environment.
Checks below remain accurately unverified and are welcome future contributions;
they are not prerequisites for sharing the existing code. Privacy review and
explicit owner approval still apply before changing repository visibility.

## Optional improvements and remaining publication step

- [ ] CI for offline tests, package installation and the chosen support matrix.
- [ ] Independent clean-machine/new-user read-only OAuth acceptance.
- [ ] Fresh independent agent walkthrough, including human consent/approval stops.
- [ ] Verify macOS/Linux before advertising tested support.
- [ ] Decide whether the initial release enables advanced writes; if included,
  obtain separate exact-plan approval for small disposable-playlist acceptance.
- [ ] Verify live recovery instructions and document operation-specific limits.
- [ ] Approve the exact candidate and repository visibility change before publishing.

## Deferred scope

GUI, whole-playlist deletion, general editing controls and scheduled writes need
separate design/implementation and approval. They are not promised release features.
