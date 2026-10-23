# YouTube Personal Library Manager

**Give your YouTube playlists a little more order.**

A small, local Python tool for exploring your playlists, finding overlap and
turning a collection of saved videos into a structure that makes sense to you.
Use it directly from the command line or let a coding agent help you plan.

## What can you do?

- **Explore your library.** See your owned playlists and their videos from a
  local inventory, without fetching everything again for each question.
- **Find duplicates and overlap.** Spot repeated entries within a playlist and
  videos shared across playlists. Keep intentional overlap.
- **Build topic playlists.** Map videos to new private playlists or add missing
  videos to existing playlists you own.
- **Preview a reorganization.** Inspect a saved plan before approving changes.
  Reading and planning work without write access.
- **Keep a local record.** Store inventory snapshots and export your library as JSON.
- **Bring your own evidence.** Import transcripts or notes and validated analysis
  JSON for evidence-grounded recipe/category proposals.

## Work with an agent

Open the repo in your coding agent and use the
[starter prompt](docs/AGENT_SETUP.md#starter-prompt). For example:

> “Which of my playlists have the most overlap?”
>
> “Help me organize these videos into topic playlists.”
>
> “Show me the proposed changes before applying anything.”

The agent uses the local gateway to inspect and plan. You handle Google login,
choose what private data to share, and approve the exact changes to your account.
No built-in AI service or local model installation is required.

## Get started

You'll need **Python 3.12+**, Git and your own Google Cloud OAuth credentials.
Everything runs on your computer; there is no server to host.

1. [Install and connect your account](docs/SETUP.md).
2. [Explore and organize playlists](docs/EDITING.md).
3. Prefer an agent? Follow the [agent guide](docs/AGENT_SETUP.md).

You can also [try the synthetic walkthrough](docs/EXAMPLE_WORKFLOW.md) without
connecting a Google account. [Troubleshooting](docs/TROUBLESHOOTING.md).

## A few boundaries

This works with **playlists owned by the connected channel**. It does not cover
all saved third-party playlists, Watch Later or watch history, and it does not
edit video footage or download transcripts. General rename/reorder controls and
whole-playlist deletion are outside the current CLI workflow.

Read-only is the default. Credentials and inventory stay outside the repo;
account changes require separate write authorization and a reviewed plan.

## Small project, shared as-is

For people who find it useful. Windows/Python 3.12 is the current usage
environment; other setups are not yet verified. Feedback and fixes are welcome.
The repository is currently private.

[MIT license](LICENSE) · [Security](SECURITY.md)
