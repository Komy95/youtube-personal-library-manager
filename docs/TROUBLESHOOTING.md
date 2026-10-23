# Troubleshooting

| Symptom | Action |
|---|---|
| Python/module not found | Use the virtual environment's Python from [Setup](SETUP.md); repeat its pip install. Python must be 3.12+. |
| Login denied | Check the selected Cloud project, enabled YouTube API, Desktop OAuth client and test-user account. Organization policy may need administrator action. |
| Credentials/data inside repository | Move them outside the clone; reset the environment paths and retry. Do not disable path protection. |
| Expired/revoked token | Re-run `auth` for read access; use `migration auth` with a valid approved plan for write access. |
| Wrong channel | Stop. Choose the intended account/channel during a new `auth` and verify `status`. Preserve the old data directory; use a new external directory for another channel. No channel-selection CLI flag exists. |
| Missing playlists | Only selected-channel owned playlists are listed. Saved third-party lists, Watch Later and history are not covered. Sync before comparing inventory. |
| Unknown video/target | Use IDs from the latest complete inventory. Unavailable videos cannot be added. Existing targets must belong to the selected channel. |
| Plan/inventory changed | Sync, regenerate assignments/plan against current state, review and approve the new full hash. Never modify a saved plan or bypass validation. |
| Approval mismatch | Compare the exact 64-character hash with the reviewed saved plan. Do not substitute a short commit hash or an old plan hash. |
| Quota exhausted | Stop writes and check your project's quota/reset. After reset, reconcile and build a fresh missing-only plan if anything was attempted. |
| HTTP 409 | Stop and inspect remote state. The status alone does not identify the cause; do not assume quota exhaustion or blindly retry. |
| Partial/uncertain write | Preserve journal and receipts. Inspect YouTube, sync, then review a new plan for missing work using existing destination IDs. Never replay a failed plan. |
| Analysis rejected | Import evidence first; use its exact ID and verbatim quote. Missing evidence cannot be replaced with title-based guesses. |

## Recovery checklist

1. Record the failed plan hash and sanitized error; keep the original database,
   journal and backup. Run `migration status --plan HASH` offline.
2. Inspect the actual destinations in YouTube. A timeout can mean a write succeeded.
3. Run `sync` when read access/quota allows; check intended memberships against
   what now exists. Do not delete created targets to restart.
4. Map assignment names to exact existing destination IDs with
   `--existing-targets-file`; use only still-missing work. Regenerate after each
   completed stage so a later stage does not use an obsolete inventory digest.
5. Review the new plan/hash and dry run, ensure quota/read headroom, then obtain
   approval before execution. An uncertain destructive result needs manual
   reconciliation, not a guessed compensating operation.

Keep reports private or sanitize them: never attach OAuth JSON, tokens,
inventories, transcripts, plan receipts or account identifiers to public issues.
Do not edit ledger rows, restore stale ledgers to retry, or invoke direct API
writes to work around a refused plan. There is no automatic remote rollback.
