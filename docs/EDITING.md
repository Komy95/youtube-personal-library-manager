# Edit playlists

Complete [Setup](SETUP.md) first. Commands below use its PowerShell variables.
Run one command at a time and stop on errors. Start with a small, disposable
destination playlist.

## 1. Inspect and back up

```powershell
& $ymPython -m youtube_manager sync
& $ymPython -m youtube_manager playlists
& $ymPython -m youtube_manager videos --playlist-id "YOUR_SOURCE_PLAYLIST_ID"
& $ymPython -m youtube_manager plan duplicates
```

Use IDs from your inventory. Cross-playlist overlap can be intentional; the
duplicate report does not delete anything. Close other manager processes and
copy the entire external data directory to a separate private backup folder.
Verify the copy contains a readable `inventory.sqlite3` before writing.
Backups preserve local records; they are not automatic remote rollback.

## 2. Save and review a plan

Create `assignments.json` in your external data directory. Each key is a target
playlist name; values are ordered video IDs from your synced inventory:

```json
{"My learning list": ["YOUR_VIDEO_ID_1", "YOUR_VIDEO_ID_2"]}
```

These are placeholders, not usable account IDs. Use the refreshed inventory from
step 1, then plan:

```powershell
$assignments = Join-Path $env:YOUTUBE_MANAGER_DATA_DIR "assignments.json"
$review = Join-Path $env:YOUTUBE_MANAGER_DATA_DIR "plan-review.json"
& $ymPython -m youtube_manager plan create --assignments-file $assignments --output $review
Get-Content $review
$planHash = (Get-Content -Raw $review | ConvertFrom-Json).hash
& $ymPython -m youtube_manager plan show --plan $planHash
& $ymPython -m youtube_manager plan validate --plan $planHash
& $ymPython -m youtube_manager migration apply --plan $planHash
```

Stop if any command fails. Review the full hash and every operation: target,
videos, additions/removals, privacy, order and protected originals. The default
dry run is local: `status: "dry_run"` does not verify remote state or quota.

By default a target creates a **new private playlist**, even when a playlist
with that title already exists. To reuse one you own, create `targets.json`
mapping the same assignment key to its exact playlist ID, then rebuild with
`--existing-targets-file PATH` and repeat review/hash extraction. For example:
`{"My learning list": "YOUR_OWNED_PLAYLIST_ID"}`.
Existing memberships are retained; only missing
videos are appended. Existing privacy/order are not rewritten to match the input.

Default plans leave source playlists/items intact. Optional
`--deduplicate-source` can remove extra copies of assigned videos within source
playlists; inspect every `remove_item` operation and its dependencies before
approving. It is not a general move or whole-playlist deletion command.

## 3. Authorize and execute

Only after you approve the exact reviewed plan, and enough quota remains:

```powershell
& $ymPython -m youtube_manager migration auth --plan $planHash --approve-plan $planHash --client-secrets $env:YOUTUBE_MANAGER_CLIENT_SECRETS
& $ymPython -m youtube_manager migration apply --plan $planHash --approve-plan $planHash --execute
```

The first command opens separate write OAuth and verifies the account; it makes
no playlist mutation. Execution checks the live account/inventory before writing.
Changed state requires a new plan and review. Tokens are stored separately.
Neither an OAuth grant nor a previous plan approves future changes.

## 4. Verify or stop

```powershell
& $ymPython -m youtube_manager migration status --plan $planHash
& $ymPython -m youtube_manager sync
& $ymPython -m youtube_manager playlists
& $ymPython -m youtube_manager videos --playlist-id "YOUR_DESTINATION_PLAYLIST_ID"
```

Check journal completion, correct owned destination, intended memberships/order,
counts and privacy (also visible in YouTube), plus unchanged protected originals.
For existing targets, compare against the previous contents plus approved additions.

On partial/uncertain failure, **stop**. Keep the database and journal; inspect
remote state and sync. Do not replay a failed plan or restore an old database to
retry it. Build a new missing-only plan against the existing destination IDs,
review its new hash, and approve it separately. [Recovery details](TROUBLESHOOTING.md).

## Optional evidence-based analysis

```powershell
& $ymPython -m youtube_manager content import --video-id "YOUR_VIDEO_ID" --file "PATH_TO_TRANSCRIPT.txt" --kind transcript
& $ymPython -m youtube_manager content list --video-id "YOUR_VIDEO_ID"
& $ymPython -m youtube_manager analysis import --video-id "YOUR_VIDEO_ID" --file "PATH_TO_ANALYSIS.json"
& $ymPython -m youtube_manager analysis list
& $ymPython -m youtube_manager analysis categories
```

Facts need exact quotes and evidence IDs; categories remain proposals. See
[the synthetic example](EXAMPLE_WORKFLOW.md) and [content rules](CONTENT_SOURCES.md).
No model server, automatic transcript download or `analysis run` is included.

The CLI currently exposes mapping-based creation/addition and optional duplicate
removal. General rename/reorder controls and whole-playlist deletion are not
supported user workflows. Video editing, all saved playlists, Watch Later and
watch history are outside scope.
