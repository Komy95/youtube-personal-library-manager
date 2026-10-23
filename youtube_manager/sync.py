"""Synchronize a YouTube account inventory into the database."""

from __future__ import annotations

from datetime import datetime, timezone

from youtube_manager.api import YouTubeClient
from youtube_manager.db import Database
from youtube_manager.models import Availability, Snapshot

_PLACEHOLDER_TITLES = {"Private video", "Deleted video"}


def synchronize(client: YouTubeClient, db: Database) -> dict:
    account = client.account()
    account_id = account.get("id")
    if not account_id:
        raise ValueError("account id is empty")

    playlists = client.list_playlists()
    for p in playlists:
        if not p.id:
            raise ValueError("playlist id is empty")

    items = []
    for p in playlists:
        for it in client.list_playlist_items(p.id):
            if not it.id:
                raise ValueError("playlist item id is empty")
            items.append(it)

    video_ids = list(dict.fromkeys(it.video_id for it in items))
    videos = client.list_videos(video_ids)
    video_map = {v.id: v for v in videos}

    for it in items:
        video = video_map.get(it.video_id)
        if video is None:
            continue
        if video.availability == Availability.unavailable:
            if it.availability in (Availability.private, Availability.deleted):
                video.availability = it.availability
            if not video.title and it.title not in _PLACEHOLDER_TITLES:
                video.title = it.title
        if it.title in _PLACEHOLDER_TITLES:
            continue
        it.availability = video.availability

    captured_at = datetime.now(timezone.utc).isoformat()
    snapshot = Snapshot(
        account_id=account_id,
        playlists=playlists,
        videos=videos,
        items=items,
        captured_at=captured_at,
    )
    db.apply_snapshot(snapshot)

    summary = db.summary()
    summary["captured_at"] = captured_at
    return summary
