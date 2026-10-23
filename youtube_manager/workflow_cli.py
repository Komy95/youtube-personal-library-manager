"""Workflow subcommands for content, analysis, planning, and migration.

These commands extend the root CLI with reviewable phases 2-5. They are
registered onto the existing root ``app`` from ``cli`` via
:func:`register_workflow`. Write/network paths are monkeypatchable so tests
can exercise them without a live account.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

import typer

from youtube_manager import migration, write_auth
from youtube_manager.config import Settings
from youtube_manager.migration import MigrationError, WriteUncertainError

_MAX_JSON_BYTES = 2 * 1024 * 1024


content_app = typer.Typer(help="Capture and inspect content evidence.")
analysis_app = typer.Typer(help="Evidence-grounded recipe analysis.")
plan_app = typer.Typer(help="Reviewable reorganization planning.")
migration_app = typer.Typer(help="Approved write migration (OAuth-gated).")


def register_workflow(root_app: typer.Typer) -> None:
    """Register the workflow subcommand groups onto the root app."""
    root_app.add_typer(content_app, name="content")
    root_app.add_typer(analysis_app, name="analysis")
    root_app.add_typer(plan_app, name="plan")
    root_app.add_typer(migration_app, name="migration")


def _to_json(obj: Any) -> str:
    def convert(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value

    return json.dumps(convert(obj))


def _read_json_file(path: str | Path) -> Any:
    p = Path(path)
    with p.open("rb") as fh:
        data = fh.read(_MAX_JSON_BYTES + 1)
    if len(data) > _MAX_JSON_BYTES:
        raise ValueError(f"file too large: exceeds {_MAX_JSON_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("file must be valid UTF-8") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("file must contain valid JSON") from exc


def _parse_assignments(raw: str | None, path: Path | None) -> dict:
    if (raw is None) == (path is None):
        raise ValueError("provide exactly one of --assignments or --assignments-file")
    if raw is not None:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError("assignments must be valid JSON") from exc
    else:
        data = _read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("assignments must be a JSON object")
    return data


def _parse_existing_targets(path: Path | None) -> dict | None:
    if path is None:
        return None
    data = _read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("existing targets must be a JSON object")
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                "existing targets must map names to string playlist IDs"
            )
    return data


def _guard(func: Callable[[], None]) -> None:
    from youtube_manager.analysis import AnalysisError
    from youtube_manager.api import ApiError
    from youtube_manager.auth import AuthError

    try:
        func()
    except (
        AuthError,
        ApiError,
        AnalysisError,
        MigrationError,
        WriteUncertainError,
        ValueError,
        KeyError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    except sqlite3.Error:
        typer.echo("Error: Database operation failed.", err=True)
        raise typer.Exit(code=1)
    except OSError:
        typer.echo("Error: A file system operation failed.", err=True)
        raise typer.Exit(code=1)


def make_write_service(credentials: Any) -> Any:
    """Build a YouTube API service from write credentials (cache disabled)."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def remote_preflight(service: Any) -> tuple[str, str]:
    """Capture a fresh read-only snapshot from the same authenticated service.

    Returns ``(account_id, inventory_digest)`` computed from an in-memory
    database. Only read APIs are used; nothing is persisted or written.
    """
    from youtube_manager import planning, sync
    from youtube_manager.api import YouTubeClient
    from youtube_manager.db import Database

    client = YouTubeClient(service)
    temp_db = Database(":memory:")
    try:
        sync.synchronize(client, temp_db)
        account_id = temp_db._get_account_id()
        digest = planning.inventory_digest(temp_db)
        return account_id, digest
    finally:
        temp_db.close()


_RUN_OP = "__run__"
_RUN_COMPLETED = "completed"


def _completed_receipts(db: Any, plan: Any) -> dict | None:
    """Return stored receipts when a plan is fully completed, else ``None``.

    Reads the reserved ``__run__`` claim and every operation row in one pass.
    A plan counts as fully completed only when the run claim is ``completed``
    and exactly every operation row is ``completed`` with no extras.
    """
    conn = db._conn
    run_status: str | None = None
    statuses: dict[str, str] = {}
    receipts: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT operation_id, status, receipt_json FROM migration_operations "
        "WHERE plan_id = ?",
        (plan.id,),
    ):
        if r["operation_id"] == _RUN_OP:
            run_status = r["status"]
            continue
        statuses[r["operation_id"]] = r["status"]
        receipts[r["operation_id"]] = json.loads(r["receipt_json"])
    op_ids = [op.id for op in plan.operations]
    if (
        run_status == _RUN_COMPLETED
        and len(statuses) == len(op_ids)
        and all(statuses.get(i) == _RUN_COMPLETED for i in op_ids)
    ):
        return {"statuses": statuses, "receipts": receipts}
    return None


# -- content ----------------------------------------------------------------


@content_app.command("import")
def content_import(
    ctx: typer.Context,
    video_id: str = typer.Option(..., "--video-id", help="Video ID to attach evidence to."),
    file: Path = typer.Option(..., "--file", help="Text or subtitle file to import."),
    kind: str = typer.Option("transcript", "--kind", help="Evidence kind."),
    source: str = typer.Option(None, "--source", help="Source label (defaults to filename)."),
    language: str = typer.Option(None, "--language", help="Language code (optional)."),
) -> None:
    """Import a transcript or notes file as content evidence."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.content import EvidenceStore
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            ev = EvidenceStore(db).import_text(
                video_id, file, kind=kind, source=source, language=language
            )
        typer.echo(_to_json(ev))

    _guard(run)


@content_app.command("list")
def content_list(
    ctx: typer.Context,
    video_id: str = typer.Option(None, "--video-id", help="Filter by video ID."),
) -> None:
    """List stored content evidence."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.content import EvidenceStore
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            items = EvidenceStore(db).list_evidence(video_id)
        typer.echo(_to_json(items))

    _guard(run)


@content_app.command("coverage")
def content_coverage(ctx: typer.Context) -> None:
    """Report evidence coverage across the inventory."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.content import EvidenceStore
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            report = EvidenceStore(db).coverage()
        typer.echo(_to_json(report))

    _guard(run)


# -- analysis ---------------------------------------------------------------


@analysis_app.command("import")
def analysis_import(
    ctx: typer.Context,
    video_id: str = typer.Option(..., "--video-id", help="Video ID to attach analysis to."),
    file: Path = typer.Option(..., "--file", help="JSON analysis payload file."),
    model: str = typer.Option("manual", "--model", help="Model label for the import."),
) -> None:
    """Import a manually authored analysis JSON, validated against evidence."""
    settings: Settings = ctx.obj

    def run() -> None:
        payload = _read_json_file(file)
        from youtube_manager.analysis import save_analysis
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            result = save_analysis(db, video_id, payload, model)
        typer.echo(_to_json(result))

    _guard(run)


@analysis_app.command("list")
def analysis_list(
    ctx: typer.Context,
    video_id: str = typer.Option(None, "--video-id", help="Filter by video ID."),
) -> None:
    """List stored analyses."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.analysis import list_analyses
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            items = list_analyses(db, video_id)
        typer.echo(_to_json(items))

    _guard(run)


@analysis_app.command("categories")
def analysis_categories(ctx: typer.Context) -> None:
    """Propose categories from analysis tags (human review, never auto-assigned)."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.analysis import category_candidates
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            candidates = category_candidates(db)
        typer.echo(_to_json(candidates))

    _guard(run)


# -- plan -------------------------------------------------------------------


@plan_app.command("duplicates")
def plan_duplicates(
    ctx: typer.Context,
    output: Path = typer.Option(None, "--output", help="Write report to a file."),
) -> None:
    """Report duplicate video memberships across playlists."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.db import Database
        from youtube_manager.planning import duplicate_report

        with Database(settings.db_path) as db:
            report = duplicate_report(db)
        text = _to_json(report)
        if output is not None:
            output.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)

    _guard(run)


@plan_app.command("create")
def plan_create(
    ctx: typer.Context,
    assignments: str = typer.Option(
        None, "--assignments", help="Inline JSON mapping target to video IDs."
    ),
    assignments_file: Path = typer.Option(
        None, "--assignments-file", help="Path to a reviewed assignments JSON file."
    ),
    existing_targets_file: Path = typer.Option(
        None,
        "--existing-targets-file",
        help="Path to a JSON object mapping target name to existing playlist ID.",
    ),
    deduplicate_source: bool = typer.Option(
        False, "--deduplicate-source", help="Remove duplicate source items."
    ),
    output: Path = typer.Option(None, "--output", help="Write full review to a file."),
) -> None:
    """Build and save a reorganization plan from explicit assignments."""
    settings: Settings = ctx.obj

    def run() -> None:
        parsed = _parse_assignments(assignments, assignments_file)
        existing_targets = _parse_existing_targets(existing_targets_file)
        from youtube_manager.db import Database
        from youtube_manager.planning import build_plan, save_plan

        with Database(settings.db_path) as db:
            plan = build_plan(
                db,
                parsed,
                deduplicate_source=deduplicate_source,
                existing_targets=existing_targets,
            )
            save_plan(db, plan)
        text = _to_json(plan)
        if output is not None:
            output.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)

    _guard(run)


@plan_app.command("show")
def plan_show(
    ctx: typer.Context,
    plan_id: str = typer.Option(..., "--plan", help="Plan ID to show."),
) -> None:
    """Show a saved plan."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.db import Database
        from youtube_manager.planning import load_plan

        with Database(settings.db_path) as db:
            plan = load_plan(db, plan_id)
        typer.echo(_to_json(plan))

    _guard(run)


@plan_app.command("validate")
def plan_validate(
    ctx: typer.Context,
    plan_id: str = typer.Option(..., "--plan", help="Plan ID to validate."),
) -> None:
    """Validate a saved plan against the current inventory."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.db import Database
        from youtube_manager.planning import load_plan, validate_plan

        with Database(settings.db_path) as db:
            plan = load_plan(db, plan_id)
            validate_plan(db, plan)
        typer.echo(_to_json({"plan_id": plan.id, "hash": plan.hash, "valid": True}))

    _guard(run)


# -- migration --------------------------------------------------------------


@migration_app.command("auth")
def migration_auth(
    ctx: typer.Context,
    plan_id: str = typer.Option(..., "--plan", help="Plan ID to authorize."),
    approve_plan: str = typer.Option(
        ..., "--approve-plan", help="Exact plan hash being approved."
    ),
    client_secrets: Path = typer.Option(
        ..., "--client-secrets", help="Path to the Google OAuth client secrets JSON."
    ),
) -> None:
    """Run write OAuth and verify the account matches the plan (no writes)."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.api import YouTubeClient
        from youtube_manager.db import Database
        from youtube_manager.planning import load_plan, validate_plan

        with Database(settings.db_path) as db:
            plan = load_plan(db, plan_id)
            validate_plan(db, plan)
            plan_hash = plan.hash
            plan_account = plan.account_id

        if approve_plan != plan_hash:
            raise MigrationError("approval does not match the plan")

        creds = write_auth.authenticate_write(settings, client_secrets, approve_plan)
        service = make_write_service(creds)
        account = YouTubeClient(service).account()
        if account.get("id") != plan_account:
            raise MigrationError("authenticated account does not match the plan account")

        typer.echo(
            _to_json(
                {
                    "plan_id": plan_id,
                    "hash": plan_hash,
                    "account": account,
                    "authenticated": True,
                }
            )
        )

    _guard(run)


@migration_app.command("apply")
def migration_apply(
    ctx: typer.Context,
    plan_id: str = typer.Option(..., "--plan", help="Plan ID to apply."),
    execute: bool = typer.Option(
        False, "--execute", help="Actually execute (requires exact approval)."
    ),
    approve_plan: str = typer.Option(None, "--approve-plan", help="Exact plan hash."),
) -> None:
    """Apply a plan (dry run by default); execute only with exact approval."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.db import Database
        from youtube_manager.planning import load_plan, validate_plan

        with Database(settings.db_path) as db:
            plan = load_plan(db, plan_id)
            plan_hash = plan.hash

            if execute:
                if approve_plan != plan_hash:
                    raise MigrationError("approval does not match the plan")

                completed = _completed_receipts(db, plan)
                if completed is not None:
                    result = {
                        "plan_id": plan.id,
                        "status": "completed_noop",
                        "statuses": completed["statuses"],
                        "receipts": completed["receipts"],
                    }
                else:
                    validate_plan(db, plan)
                    creds = write_auth.load_write_credentials(settings, plan_hash)
                    service = make_write_service(creds)
                    writer = migration.YouTubeWriter(service)

                    def preflight() -> tuple[str, str]:
                        account_id, digest = remote_preflight(service)
                        if account_id != plan.account_id:
                            raise MigrationError(
                                "write account does not match the plan account"
                            )
                        if digest != plan.inventory_digest:
                            raise MigrationError(
                                "inventory changed since the plan was built"
                            )
                        return account_id, digest

                    result = migration.execute_plan(
                        db, plan, writer, plan_hash, preflight
                    )
            else:
                validate_plan(db, plan)
                result = {
                    "hash": plan_hash,
                    "operations": [op.model_dump(mode="json") for op in plan.operations],
                    "status": "dry_run",
                }

        typer.echo(_to_json(result))

    _guard(run)


@migration_app.command("status")
def migration_status(
    ctx: typer.Context,
    plan_id: str = typer.Option(..., "--plan", help="Plan ID to inspect."),
) -> None:
    """Report the ledger of migration operation statuses (offline)."""
    settings: Settings = ctx.obj

    def run() -> None:
        from youtube_manager.db import Database

        with Database(settings.db_path) as db:
            rows = db._conn.execute(
                "SELECT operation_id, status, receipt_json, updated_at "
                "FROM migration_operations WHERE plan_id = ? "
                "ORDER BY updated_at, operation_id",
                (plan_id,),
            ).fetchall()
        receipts = [
            {
                "operation_id": r["operation_id"],
                "status": r["status"],
                "receipt": json.loads(r["receipt_json"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
        typer.echo(_to_json({"plan_id": plan_id, "operations": receipts}))

    _guard(run)
