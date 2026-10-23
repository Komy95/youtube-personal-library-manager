# Distribution plan

Updated 2026-10-24. [Release checklist](PUBLIC_RELEASE_ROADMAP.md).

## Product and installation model

Distribute source and instructions for a local YouTube playlist manager. Every
user creates their own Google Cloud project/Desktop OAuth client and retains
credentials, SQLite inventory, evidence and plans outside the clone. No hosted
service, shared OAuth client, centralized storage or model runtime is required.

The first interface is the CLI plus optional conversational agent assistance.
"Editor" means playlist organization, not video-footage editing. The application
covers selected-channel owned playlists; it does not provide all saved playlists,
Watch Later/history or general transcript downloads.

## Architecture and invariants

Retain the existing dependency flow: config/models -> auth/API -> DB/sync ->
content/analysis -> planning -> migration/write_auth -> CLI. Preserve account
binding, complete snapshots, history/order/duplicates, exact scopes, private new
destinations, hash-bound approval, remote preflight, journal-before-write and
blocked automatic retries after uncertain non-idempotent writes.

Analysis is evidence-validated JSON import, prepared manually or with consented
agent assistance. Content claims cite stored evidence and exact quotes; categories
remain proposals. Metadata-based organization suggestions are labeled as such.

## Delivered documentation

- [Setup](SETUP.md): Python/venv/pip, paths, first read-only connection, updates.
- [Google OAuth](GOOGLE_OAUTH.md): per-user Cloud project, audience, Desktop client,
  account/channel checks, separate write scopes and token expiration.
- [Editing](EDITING.md): explicit mappings, existing targets, backups, review/hash,
  local dry run, write approval, preflight, verification and recovery.
- [Agent workflow](AGENT_SETUP.md): starter prompt, consent, minimum read-only
  queries, untrusted-content boundaries, approval packets and stop conditions.
- [Troubleshooting](TROUBLESHOOTING.md) and [synthetic example](EXAMPLE_WORKFLOW.md).
- [Security](../SECURITY.md), [contribution/support](../CONTRIBUTING.md),
  [development](DEVELOPMENT.md) and [dependency licenses](THIRD_PARTY_NOTICES.md).

## Publication boundary

Ship source, synthetic tests and generic documentation under MIT. Do not ship
account data, OAuth/token files, personal plans/receipts, transcripts, private
operation history, tool histories, environments or backups. Ignore rules are a
secondary safeguard; inspect every staged path and full candidate history.

Before publication, use a maintained local secret scanner plus manual review of
private paths, identifiers, author metadata and inherited permissions. Revoke
credentials if exposed; history rewriting alone cannot revoke or guarantee
removal from provider caches or third-party copies. Keep recovery archives private
and outside published refs. [Audit scope/results](DISTRIBUTION_AUDIT.md).

Each new user gives their own consent for external AI data processing and each
concrete account mutation. Documentation and a previous OAuth grant never approve
a future plan. Contributor-specific tools are not application requirements.

## Packaging and verification

Current evidence: Windows/Python 3.12.10 fresh-environment editable installation,
CLI help and dependency consistency; **276 offline tests passed, 2 platform skips**.
This does not prove independent new-user setup or cross-platform acceptance.

Next, add credential-free CI and installation from built wheels on the chosen
OS/Python matrix. Check licenses/notices for the resolved dependency versions.
Never put real account fixtures or YouTube-write credentials in CI.

Verify a clean-machine read-only setup and an independent agent walkthrough.
If advanced writes are in the first release, use a small disposable playlist and
a separately human-approved exact plan, verify remote results and record only
sanitized operation-specific evidence. Stop on partial/uncertain outcomes.
General rename/reorder interfaces and whole-playlist deletion remain deferred.

## Release decision

Share the repository as a small, as-is community project using the current
verified Windows baseline and clearly stated limits. CI, independent-user/agent
walkthroughs and additional-platform testing are optional contributions, not
maintainer commitments or publication prerequisites. Do not claim those checks
have passed. Present the audited candidate and known limits to the owner for
publication approval, then change visibility and verify it. Tags/release notes
can be added when useful. No hosting or domain setup is involved.
