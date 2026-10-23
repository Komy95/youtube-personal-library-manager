"""Read-only YouTube Data API client for the personal library inventory."""

from __future__ import annotations

import json
import time
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import ServerNotFoundError

from youtube_manager.models import Availability, Playlist, PlaylistItem, Video

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
AUTH_STATUSES = {401, 403}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
WHITELISTED_REASONS = QUOTA_REASONS

_REASON_MESSAGES = {
    "quotaExceeded": "YouTube API quota exceeded.",
    "dailyLimitExceeded": "YouTube API daily limit exceeded.",
    "authError": "YouTube API authentication failed.",
}

_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


class ApiError(Exception):
    """Sanitized API error exposing only a status and whitelisted reason."""

    def __init__(self, message: str, status: int | None = None, reason: str = "apiError"):
        super().__init__(message)
        self.status = status
        self.reason = reason


class YouTubeClient:
    """Thin wrapper over an injected Google discovery service."""

    def __init__(self, service: Any, sleep: Any = time.sleep, max_retries: int = 3):
        self.service = service
        self._sleep = sleep
        self.max_retries = max_retries

    def execute(self, request: Any) -> dict:
        reason = "apiError"
        status = None
        for attempt in range(self.max_retries + 1):
            try:
                return request.execute()
            except RefreshError:
                raise ApiError(self._message("authError"), status=401, reason="authError") from None
            except HttpError as exc:
                status, reason = self._classify_http(exc)
                if reason in QUOTA_REASONS or status in AUTH_STATUSES or status not in RETRYABLE_STATUSES:
                    raise ApiError(self._message(reason), status=status, reason=reason) from None
            except (OSError, ServerNotFoundError):
                status = None
                reason = "apiError"
            if attempt >= self.max_retries:
                break
            self._sleep(self._delay(attempt))
        raise ApiError(self._message(reason), status=status, reason=reason)

    def account(self) -> dict:
        request = self.service.channels().list(part="snippet", mine=True, maxResults=50)
        data = self._snapshot(self.execute(request))
        items = data.get("items") or []
        if not items:
            raise ApiError("No YouTube channel associated with this account.", reason="noChannel")
        item = items[0]
        snippet = item.get("snippet", {})
        if not item.get("id"):
            raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
        return {"id": item.get("id"), "title": snippet.get("title")}

    def list_playlists(self) -> list[Playlist]:
        playlists: list[Playlist] = []
        for data in self._paginate(
            lambda token: self.service.playlists().list(
                mine=True, maxResults=50, part="snippet,contentDetails,status", pageToken=token
            )
        ):
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                status = item.get("status", {})
                pid = item.get("id", "")
                if not pid:
                    raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
                playlists.append(
                    Playlist(
                        id=pid,
                        title=snippet.get("title", ""),
                        description=snippet.get("description", ""),
                        channel_id=snippet.get("channelId", ""),
                        channel_title=snippet.get("channelTitle", ""),
                        published_at=snippet.get("publishedAt"),
                        privacy_status=status.get("privacyStatus", "unavailable"),
                        item_count=int(content.get("itemCount", 0) or 0),
                    )
                )
        return playlists

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistItem]:
        items: list[PlaylistItem] = []
        for data in self._paginate(
            lambda token: self.service.playlistItems().list(
                playlistId=playlist_id,
                maxResults=50,
                part="snippet,contentDetails,status",
                pageToken=token,
            )
        ):
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                resource_id = snippet.get("resourceId", {})
                video_id = content.get("videoId") or resource_id.get("videoId")
                if not video_id:
                    raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
                item_id = item.get("id", "")
                if not item_id:
                    raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
                items.append(
                    PlaylistItem(
                        id=item_id,
                        playlist_id=playlist_id,
                        video_id=video_id,
                        position=int(snippet.get("position", 0) or 0),
                        title=snippet.get("title", ""),
                        availability=self._item_availability(item),
                    )
                )
        items.sort(key=lambda i: (i.position, i.id, i.video_id))
        return items

    def list_videos(self, video_ids: list[str]) -> list[Video]:
        dedup = list(dict.fromkeys(video_ids))
        found: dict[str, Video] = {}
        for i in range(0, len(dedup), 50):
            batch = dedup[i : i + 50]
            request = self.service.videos().list(
                id=",".join(batch), part="snippet,contentDetails,status"
            )
            data = self._snapshot(self.execute(request))
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                status = item.get("status", {})
                vid = item.get("id", "")
                privacy = status.get("privacyStatus")
                availability = (
                    Availability.private if privacy == "private" else Availability.available
                )
                found[vid] = Video(
                    id=vid,
                    title=snippet.get("title", ""),
                    description=snippet.get("description", ""),
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    duration=content.get("duration"),
                    privacy_status=privacy,
                    availability=availability,
                    published_at=snippet.get("publishedAt"),
                )
        return [
            found.get(vid, Video(id=vid, availability=Availability.unavailable)) for vid in dedup
        ]

    def _paginate(self, make_request: Any) -> Any:
        token = None
        seen: set[str] = set()
        while True:
            request = make_request(token)
            data = self._snapshot(self.execute(request))
            yield data
            token = data.get("nextPageToken")
            if not token:
                break
            if token in seen:
                raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
            seen.add(token)

    def _snapshot(self, data: Any) -> dict:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ApiError("Snapshot incomplete.", reason="incompleteSnapshot")
        return data

    def _item_availability(self, item: dict) -> Availability:
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        title = (snippet.get("title") or "").strip()
        if title == "Private video":
            return Availability.private
        if title == "Deleted video":
            return Availability.deleted
        if status.get("privacyStatus") == "private":
            return Availability.private
        return Availability.available

    def _classify_http(self, exc: HttpError) -> tuple[int | None, str]:
        status = exc.resp.status if getattr(exc, "resp", None) is not None else None
        reason = "apiError"
        content = getattr(exc, "content", None)
        if content:
            try:
                if isinstance(content, bytes):
                    content = content.decode("utf-8", "replace")
                data = json.loads(content)
                errors = data.get("error", {}).get("errors", [])
                if errors:
                    r = errors[0].get("reason")
                    if r in WHITELISTED_REASONS:
                        reason = r
            except (ValueError, TypeError, AttributeError):
                pass
        if status in AUTH_STATUSES and reason not in QUOTA_REASONS:
            reason = "authError"
        return status, reason

    def _message(self, reason: str) -> str:
        return _REASON_MESSAGES.get(reason, "YouTube API request failed.")

    def _delay(self, attempt: int) -> float:
        return min(_BASE_DELAY * (2**attempt), _MAX_DELAY)
