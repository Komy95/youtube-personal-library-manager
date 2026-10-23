# Distribution audit

Date: 2026-10-24. Scope: candidate source, generic documentation, synthetic tests,
package metadata and Git history intended for this repository's distribution.
This is a bounded audit, not a guarantee of absence of every possible secret.

## Original history

Gitleaks **8.30.1**, default rules with fully redacted output and inline allow
comments ignored, scanned all reachable Git refs. The scanner reported **4 commits,
approximately 484 KB, no secret findings**. A local recovery bundle was created
and verified before removing publication history. Neither that archive nor raw
scanner reports are part of the distributed file set.

Manual review found personal operation logs and account-specific approvals in
old documentation, plus personal Git author email metadata. These are privacy
findings even though a secret scanner does not classify them as credentials.
Generic synthetic account IDs/test paths are fixtures, not live account records.

## Candidate cleanup

- Replaced operational/usage logs with product capabilities, current offline
  evidence and remaining release acceptance.
- Removed inherited account approvals and kept explicit per-user consent rules.
- Removed unrelated provider-switching scripts from the distribution.
- Reviewed tracked paths and added ignore safeguards for account artifacts.
- Added the selected MIT source license, packaging metadata and dependency notes.
- Kept current user-facing examples synthetic and credentials outside the clone.

The exact staged candidate tree (approximately 370 KB) and extracted wheel
(approximately 124 KB) passed local Gitleaks scans with no findings. The wheel
contains `License-Expression: MIT`, declares and includes the identical root
LICENSE, and omits the unrelated utilities. Wheel installation and CLI/dependency
checks passed outside the source checkout; `pip check` found no broken
requirements. Full offline suite after cleanup: **276 passed, 2 skipped in 7.74s**.
Runtime source/tests were unchanged. All 47 tracked candidate paths were inspected;
ignore-rule probes and 69 relative Markdown links/anchors passed.

The clean replacement was uploaded and cloned afresh from GitHub. It had a
single sanitized root and only `main`; Gitleaks scanned that history and its
files with no findings. GitHub reports a distinct repository identity, private
visibility and MIT license detection. The original is a separately named private,
read-only archive. A pre-cleanup commit returned HTTP 404 in the replacement.
Follow-up audit-summary commits contain generic verification records only.
This report is not publication or account-write approval.

## Retention boundary

Privacy recheck (2026-10-24): reviewed all four published commits through
`826e0cb` and 55 distinct tracked text blobs, including author/committer metadata.
No known private name/email/path/operation markers or email addresses in file
content were found. Fresh-clone file and full-history Gitleaks scans again had
no findings. Commit attribution uses the owner's public GitHub username and
GitHub noreply identity; the clone URL also identifies the repository owner.
The repository had only `main`, no tags, issues/pull requests, releases or Actions
workflows. Both the clean repository and the separate archived original were
confirmed private. Ignored local recovery/tool state and the private archive are
outside the distribution and intentionally retain historical records.

The owner selected a clean replacement repository, with the original retained
as a separately named private archive, to avoid carrying old Git objects into
the future public repository. The archive must remain private. Local recovery
archives and tool-managed checkpoint refs must never be mirrored or pushed to
the replacement. Push only its sanitized `main`, using public noreply author
metadata. This does not erase backups, previous clones or provider retention
of the original archive. No credential rotation was indicated by the findings;
any later discovered real credential must still be revoked at its provider.

## Reproduce the secret checks

Install Gitleaks from its [official releases](https://github.com/gitleaks/gitleaks/releases),
verify the release checksum, and run locally from a disposable clean clone:

```text
gitleaks git . --log-opts="--all" --redact=100 --ignore-gitleaks-allow --no-banner
gitleaks dir . --redact=100 --ignore-gitleaks-allow --no-banner
```

Keep detailed reports private and redacted. Review Git author metadata, personal
operation text and staged filenames separately; pattern-based scans do not
replace these checks. Installed dependency metadata review is described in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
