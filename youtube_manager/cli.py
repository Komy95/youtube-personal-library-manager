"""Command-line interface for the YouTube Personal Library Manager."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

import typer
from googleapiclient.discovery import build

from youtube_manager import auth as auth_module
from youtube_manager.api import ApiError, YouTubeClient
from youtube_manager.auth import AuthError
from youtube_manager.config import Settings, load_settings
from youtube_manager.db import Database

app = typer.Typer()


def make_client(credentials: Any) -> YouTubeClient:
    """Build a read-only YouTube API client from OAuth credentials."""
    service = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    return YouTubeClient(service)


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _guard(func: Callable[[], None]) -> None:
    try:
        func()
    except AuthError as exc:
        typer.echo(f"Error: {exc}", err=True)
    except ApiError as exc:
        typer.echo(f"Error: {exc}", err=True)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
    except sqlite3.Error:
        typer.echo("Error: Database operation failed.", err=True)
    except OSError:
        typer.echo("Error: A file system operation failed.", err=True)
    else:
        return
    raise typer.Exit(code=1)


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: Path = typer.Option(
        None,
        "--data-dir",
        help="Directory for the inventory database and stored credentials.",
    ),
) -> None:
    if data_dir is None:
        settings = load_settings()
    else:
        settings = Settings(data_dir=data_dir)
    ctx.obj = settings


@app.command()
def auth(
    ctx: typer.Context,
    client_secrets: Path = typer.Option(
        None,
        "--client-secrets",
        help="Path to the Google OAuth client secrets JSON file.",
    ),
) -> None:
    """Run the OAuth flow and verify the authenticated account."""
    settings: Settings = ctx.obj
    secrets = client_secrets
    if secrets is None:
        env_val = os.environ.get("YOUTUBE_MANAGER_CLIENT_SECRETS")
        if env_val:
            secrets = Path(env_val)
    if secrets is None:
        _fail(
            "A client secrets file is required. Pass --client-secrets or set "
            "YOUTUBE_MANAGER_CLIENT_SECRETS."
        )

    def run() -> None:
        creds = auth_module.authenticate(settings, secrets)
        client = make_client(creds)
        account = client.account()
        typer.echo(json.dumps(account))

    _guard(run)


@app.command()
def status(ctx: typer.Context) -> None:
    """Report the authenticated account and the local inventory summary."""
    settings: Settings = ctx.obj

    def run() -> None:
        creds = auth_module.load_credentials(settings)
        client = make_client(creds)
        account = client.account()
        if settings.db_path.exists():
            with Database(settings.db_path) as db:
                summary = db.summary()
        else:
            summary = None
        typer.echo(json.dumps({"account": account, "database": summary}))

    _guard(run)


@app.command()
def sync(ctx: typer.Context) -> None:
    """Synchronize the local inventory with the YouTube account."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager import sync as sync_module

        creds = auth_module.load_credentials(settings)
        client = make_client(creds)
        with Database(settings.db_path) as db:
            result = sync_module.synchronize(client, db)
        typer.echo(json.dumps(result))

    _guard(run)


@app.command()
def playlists(
    ctx: typer.Context,
    include_removed: bool = typer.Option(
        False, "--include-removed", help="Include removed playlists."
    ),
) -> None:
    """List playlists from the local inventory."""
    settings: Settings = ctx.obj

    def run() -> None:
        with Database(settings.db_path) as db:
            data = db.list_playlists(include_removed=include_removed)
        typer.echo(json.dumps(data))

    _guard(run)


@app.command()
def videos(
    ctx: typer.Context,
    playlist_id: str = typer.Option(..., "--playlist-id", help="Playlist ID to list."),
    include_removed: bool = typer.Option(
        False, "--include-removed", help="Include removed items."
    ),
) -> None:
    """List videos for a playlist from the local inventory."""
    settings: Settings = ctx.obj

    def run() -> None:
        with Database(settings.db_path) as db:
            data = db.list_videos(playlist_id, include_removed=include_removed)
        typer.echo(json.dumps(data))

    _guard(run)


@app.command()
def export(
    ctx: typer.Context,
    format: str = typer.Option("json", "--format", help="Export format (json only)."),
    output: Path = typer.Option(
        None, "--output", help="Write output to a file instead of stdout."
    ),
) -> None:
    """Export the full inventory, including historical data, as JSON."""
    settings: Settings = ctx.obj
    if format != "json":
        _fail(f"Unsupported export format: {format!r}. Only 'json' is supported.")

    def run() -> None:
        with Database(settings.db_path) as db:
            data = db.export()
        text = json.dumps(data, indent=2)
        if output is not None:
            output.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)

    _guard(run)


def _register_workflow() -> None:
    from youtube_manager.workflow_cli import register_workflow

    register_workflow(app)


_register_workflow()


if __name__ == "__main__":
    app()
