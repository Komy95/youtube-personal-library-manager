# Synthetic walkthrough

Everything below is synthetic. `v1`, `PL1` and `EVIDENCE_ID` are not real
account identifiers. Do not execute these examples against your own account.

The existing offline integration test exercises this sequence without Google
credentials, network requests, or YouTube writes:

```powershell
& $ymPython -m pip install -e ".[dev]"
& $ymPython -m pytest -q tests/test_workflow_cli.py::test_full_offline_workflow
```

It creates its own temporary database; your configured real data directory is
not used. The expected result is one passed test.

| Step | Synthetic input | Checked result |
|---|---|---|
| Inventory | Channel `UC1`, playlist `PL1`, video `v1` | Local snapshot exists |
| Evidence import | `Mix 2 cups flour with 1 egg.` | Evidence has a generated ID |
| Analysis import | Facts cite that ID and exact substrings | Stored analysis is a recipe |
| Category inspection | Proposed tag `baking` | Category is marked `proposed: true` |
| Plan creation | `{"baking": ["v1"]}` | Saved plan has matching `id` and `hash` |
| Local validation | Generated plan hash | `valid: true` |
| Migration preview | Apply without `--execute` | `status: "dry_run"` with operations |
| Journal inspection | Same plan hash | Status is inspectable without auth |

The imported JSON has this shape (replace `EVIDENCE_ID` with the generated ID
only in a deliberately synthetic environment):

```json
{
  "is_recipe": true,
  "title": {"value": "Flour Cake", "evidence_id": "EVIDENCE_ID", "quote": "flour"},
  "ingredients": [{"value": "flour", "evidence_id": "EVIDENCE_ID", "quote": "flour"}],
  "steps": [{"value": "mix", "evidence_id": "EVIDENCE_ID", "quote": "Mix"}],
  "tags": [{"value": "baking", "evidence_id": "EVIDENCE_ID", "quote": "flour"}]
}
```

A grounded quote proves the supplied text contains that statement; it does not
independently verify the video or approve the category. No live write acceptance
or clean-machine OAuth acceptance is established by this walkthrough.
