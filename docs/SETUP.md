# Setup

Use your own Google project and account. Nothing is hosted by the maintainer.
Windows/PowerShell is the verified development environment; macOS/Linux steps
below are provided but have not been verified on those systems.

## 1. Install

Install [Python 3.12+](https://www.python.org/downloads/) and
[Git](https://git-scm.com/downloads). Python 3.12 is the currently tested version.
The repository is currently private; cloning requires access from its owner.

```powershell
git clone https://github.com/Komy95/youtube-personal-library-manager.git
cd youtube-personal-library-manager
python --version  # must be 3.12 or newer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
$ymPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $ymPython -m youtube_manager --help
```

No environment activation, AI provider, CodeGraph, or worker service is needed.
Optional alternative: `uv venv --python 3.12 .venv`, then
`uv pip install --python .venv/Scripts/python.exe -e .`.

## 2. Configure Google and local paths

Follow [Google setup](GOOGLE_OAUTH.md), then set these in PowerShell:

```powershell
$env:YOUTUBE_MANAGER_DATA_DIR = Join-Path $HOME "youtube-editor-data"
$env:YOUTUBE_MANAGER_CLIENT_SECRETS = Join-Path $HOME "youtube-editor-secrets/client.json"
```

Save the downloaded OAuth JSON at that second path. Both paths must be outside
the clone. These settings and `$ymPython` last only for the current shell; set
them again when opening a new one. Use a separate data directory per channel.
Do not put private assignments, exports or evidence inside the clone either.

## 3. Connect read-only

Run from the clone, using the same shell:

```powershell
& $ymPython -m youtube_manager auth
& $ymPython -m youtube_manager status
```

Complete Google login yourself. Check the returned channel ID/title before
continuing. If it is wrong, see [wrong channel](TROUBLESHOOTING.md).

```powershell
& $ymPython -m youtube_manager sync
& $ymPython -m youtube_manager playlists
```

Success means `sync` completes and your owned playlists appear. Local lists may
be empty for a channel with no owned playlists. Only `auth` opens login;
`status` and `sync` use online reads. See [editing](EDITING.md) for later changes.

## macOS/Linux (not yet platform-verified)

Clone and enter the repository as above, then:

```bash
python3 --version  # must be 3.12 or newer
python3 -m venv .venv
.venv/bin/python -m pip install -e .
export YOUTUBE_MANAGER_DATA_DIR="$HOME/youtube-editor-data"
export YOUTUBE_MANAGER_CLIENT_SECRETS="$HOME/youtube-editor-secrets/client.json"
.venv/bin/python -m youtube_manager auth
.venv/bin/python -m youtube_manager status
.venv/bin/python -m youtube_manager sync
.venv/bin/python -m youtube_manager playlists
```

Use `.venv/bin/python` instead of `& $ymPython` in other guides. Credentials use
restrictive filesystem permissions on POSIX; Windows tokens use DPAPI encryption.

## Update or remove

Before upgrading, stop manager processes and back up the entire external data
directory. From the clone: `git pull --ff-only`, then repeat the pip install.
Review release notes before using changed write behavior.

To uninstall: `& $ymPython -m pip uninstall youtube-personal-library-manager`.
Remove the clone/virtual environment separately if wanted. Local account data
remains outside it; revoke the OAuth application's access in your Google account
when you stop using it. [Troubleshooting](TROUBLESHOOTING.md).
