# YouTube Personal Library Manager

Run your own YouTube playlist manager locally with Python and your Google account.
Inspect owned playlists, prepare explicit assignments, preview changes and execute
only a plan you approve. No hosted service or model runtime is required.

**Private preparation:** cloning currently requires repository access. The code
has offline coverage; independent new-user setup,
CI and cross-platform acceptance remain open. Distribution cleanup and the
license audit are recorded in [the audit report](docs/DISTRIBUTION_AUDIT.md).

## Start here

- **People:** [Setup](docs/SETUP.md) -> [Google login setup](docs/GOOGLE_OAUTH.md)
  -> [Edit playlists](docs/EDITING.md).
- **Agents:** use the starter prompt and workflow in [Agent setup](docs/AGENT_SETUP.md).
- **Try without an account:** [Synthetic walkthrough](docs/EXAMPLE_WORKFLOW.md).
- **Problems:** [Troubleshooting](docs/TROUBLESHOOTING.md).

Requirements: Git, Python 3.12+ and your own Google Cloud project with YouTube
Data API v3 enabled. Windows/Python 3.12 is the current verification environment;
macOS/Linux instructions are provided but not yet verified on those platforms.

## Scope and privacy

Read-only access is the default. Credentials and inventory stay outside the
repository. Editing requires separate write OAuth, a saved plan and approval of
its full hash. Failed or uncertain writes must be reconciled, never blindly retried.
AI assistance is optional; sharing private records with a provider needs your consent.

The CLI supports new private playlists, additions to owned targets and optional
within-playlist duplicate removal. It does not expose general rename/reorder or
whole-playlist deletion workflows. It does not edit video footage, retrieve all
saved playlists/Watch Later/history, or download third-party transcripts.

Optional [content evidence](docs/CONTENT_SOURCES.md) and analysis JSON import
validate cited quotes locally. No automatic analysis/model-server integration exists.

## Project records

[Release roadmap](docs/PUBLIC_RELEASE_ROADMAP.md) ·
[Detailed release plan](docs/OPEN_SOURCE_PLAN.md) ·
[Development](docs/DEVELOPMENT.md) · [Project roadmap](ROADMAP.md) ·
[MIT license](LICENSE) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

Latest full offline suite: **276 passed, 2 skipped** in a fresh Windows/Python
3.12.10 virtual environment (2026-10-24). Installation, CLI help and dependency
consistency passed; independent new-user OAuth/agent acceptance remains open.
