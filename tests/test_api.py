"""Tests for the read-only YouTube Data API client."""

from __future__ import annotations

import json
import traceback
from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import ServerNotFoundError

from youtube_manager.api import ApiError, YouTubeClient
from youtube_manager.models import Availability


class FakeRequest:
    def __init__(self, result=None, error=None, outcomes=None):
        self.result = result
        self.error = error
        self.outcomes = outcomes
        self.executions = 0

    def execute(self):
        if self.outcomes is not None:
            outcome = self.outcomes[min(self.executions, len(self.outcomes) - 1)]
        elif self.error is not None:
            outcome = self.error
        else:
            outcome = self.result
        self.executions += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeService:
    def __init__(self):
        self.calls = []
        self.queue = []

    def _list(self, name):
        def list_method(**kwargs):
            self.calls.append((name, kwargs))
            assert self.queue, f"unexpected request to {name}"
            return self.queue.pop(0)

        return list_method

    def channels(self):
        return SimpleNamespace(list=self._list("channels.list"))

    def playlists(self):
        return SimpleNamespace(list=self._list("playlists.list"))

    def playlistItems(self):
        return SimpleNamespace(list=self._list("playlistItems.list"))

    def videos(self):
        return SimpleNamespace(list=self._list("videos.list"))


def http_error(status, reason=None):
    resp = SimpleNamespace(status=status, reason="error")
    if reason is not None:
        content = json.dumps(
            {"error": {"errors": [{"reason": reason, "message": "boom"}]}}
        ).encode()
    else:
        content = b'{"error": {"errors": [{"reason": "someUnknownReason"}]}}'
    return HttpError(resp, content)


def playlist_raw(pid, title, **extra):
    item = {
        "id": pid,
        "snippet": {"title": title, "channelId": "CH", "channelTitle": "Chan"},
        "contentDetails": {"itemCount": 3},
        "status": {"privacyStatus": "public"},
    }
    item.update(extra)
    return item


def item_raw(iid, video_id, position=0, title="T", status=None):
    return {
        "id": iid,
        "snippet": {
            "title": title,
            "position": position,
            "resourceId": {"videoId": video_id},
        },
        "contentDetails": {"videoId": video_id},
        "status": status or {"privacyStatus": "public"},
    }


def video_raw(vid, title="V", privacy="public"):
    return {
        "id": vid,
        "snippet": {
            "title": title,
            "channelId": "CH",
            "channelTitle": "Chan",
            "publishedAt": "2020-01-01T00:00:00Z",
        },
        "contentDetails": {"duration": "PT1M"},
        "status": {"privacyStatus": privacy},
    }


def test_account_returns_id_and_title():
    svc = FakeService()
    svc.queue = [FakeRequest({"items": [{"id": "UC1", "snippet": {"title": "Me"}}]})]
    result = YouTubeClient(svc).account()
    assert result == {"id": "UC1", "title": "Me"}
    assert svc.calls[0] == ("channels.list", {"part": "snippet", "mine": True, "maxResults": 50})


def test_account_no_channel_raises():
    svc = FakeService()
    svc.queue = [FakeRequest({"items": []})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).account()
    assert exc.value.reason == "noChannel"


def test_list_playlists_paginates():
    svc = FakeService()
    svc.queue = [
        FakeRequest({"items": [playlist_raw("PL1", "A")], "nextPageToken": "t1"}),
        FakeRequest({"items": [playlist_raw("PL2", "B")]}),
    ]
    result = YouTubeClient(svc).list_playlists()
    assert [p.id for p in result] == ["PL1", "PL2"]
    assert result[0].privacy_status == "public"
    assert result[0].item_count == 3
    assert len(svc.calls) == 2
    assert svc.calls[0][1]["pageToken"] is None
    assert svc.calls[1][1]["pageToken"] == "t1"


def test_list_playlist_items_sorted_and_paginated():
    svc = FakeService()
    svc.queue = [
        FakeRequest(
            {
                "items": [
                    item_raw("i2", "v2", position=2),
                    item_raw("i1", "v1", position=1),
                ],
                "nextPageToken": "t1",
            }
        ),
        FakeRequest({"items": [item_raw("i3", "v1", position=1)]}),
    ]
    result = YouTubeClient(svc).list_playlist_items("PL")
    assert [(i.id, i.position) for i in result] == [("i1", 1), ("i3", 1), ("i2", 2)]
    assert result[0].playlist_id == "PL"


def test_list_playlist_items_missing_video_id_raises():
    svc = FakeService()
    raw = item_raw("i1", "v1")
    del raw["contentDetails"]["videoId"]
    raw["snippet"]["resourceId"] = {"videoId": ""}
    svc.queue = [FakeRequest({"items": [raw]})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).list_playlist_items("PL")
    assert exc.value.reason == "incompleteSnapshot"


def test_list_videos_dedupes_and_batches_50():
    svc = FakeService()
    ids = [f"v{i}" for i in range(51)] + ["v0", "v1"]
    svc.queue = [
        FakeRequest({"items": [video_raw(f"v{i}") for i in range(50)]}),
        FakeRequest({"items": [video_raw("v50")]}),
    ]
    result = YouTubeClient(svc).list_videos(ids)
    assert len(result) == 51
    assert [v.id for v in result] == [f"v{i}" for i in range(51)]
    assert len(svc.calls) == 2
    first_ids = svc.calls[0][1]["id"].split(",")
    second_ids = svc.calls[1][1]["id"].split(",")
    assert len(first_ids) == 50
    assert second_ids == ["v50"]


def test_list_videos_missing_ids_unavailable():
    svc = FakeService()
    svc.queue = [FakeRequest({"items": [video_raw("v1")]})]
    result = YouTubeClient(svc).list_videos(["v1", "v2"])
    assert result[0].availability == Availability.available
    assert result[1].availability == Availability.unavailable


def test_list_videos_private_status():
    svc = FakeService()
    svc.queue = [FakeRequest({"items": [video_raw("v1", privacy="private")]})]
    result = YouTubeClient(svc).list_videos(["v1"])
    assert result[0].availability == Availability.private


@pytest.mark.parametrize(
    "raw,expected",
    [
        (item_raw("i", "v", title="Private video"), Availability.private),
        (item_raw("i", "v", title="Deleted video"), Availability.deleted),
        (item_raw("i", "v", status={"privacyStatus": "private"}), Availability.private),
        (item_raw("i", "v"), Availability.available),
    ],
)
def test_item_availability_classification(raw, expected):
    svc = FakeService()
    svc.queue = [FakeRequest({"items": [raw]})]
    result = YouTubeClient(svc).list_playlist_items("PL")
    assert result[0].availability == expected


def test_quota_error_fails_immediately_without_retry():
    svc = FakeService()
    svc.queue = [FakeRequest(error=http_error(429, "quotaExceeded"))]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append)
    with pytest.raises(ApiError) as exc:
        client.account()
    assert exc.value.reason == "quotaExceeded"
    assert exc.value.status == 429
    assert "http" not in str(exc.value).lower()
    assert sleeps == []
    assert len(svc.calls) == 1


def test_auth_error_fails_immediately():
    svc = FakeService()
    svc.queue = [FakeRequest(error=http_error(403))]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).account()
    assert exc.value.reason == "authError"
    assert exc.value.status == 403
    assert len(svc.calls) == 1


def test_transient_error_retries_with_noop_sleep():
    svc = FakeService()
    request = FakeRequest(
        outcomes=[
            http_error(503),
            {"items": [{"id": "UC1", "snippet": {"title": "Me"}}]},
        ]
    )
    svc.queue = [request]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append)
    result = client.account()
    assert result["id"] == "UC1"
    assert len(svc.calls) == 1
    assert sleeps == [1.0]
    assert request.executions == 2


def test_oserror_retries():
    svc = FakeService()
    request = FakeRequest(
        outcomes=[
            OSError("boom"),
            {"items": [{"id": "UC1", "snippet": {"title": "Me"}}]},
        ]
    )
    svc.queue = [request]
    result = YouTubeClient(svc, sleep=lambda *_: None).account()
    assert result["id"] == "UC1"
    assert request.executions == 2


def test_server_not_found_retries_then_succeeds():
    svc = FakeService()
    request = FakeRequest(
        outcomes=[
            ServerNotFoundError("Unable to resolve host 'www.googleapis.com'"),
            {"items": [{"id": "UC1", "snippet": {"title": "Me"}}]},
        ]
    )
    svc.queue = [request]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append)
    result = client.account()
    assert result["id"] == "UC1"
    assert request.executions == 2
    assert sleeps == [1.0]


def test_server_not_found_exhausted_sanitized():
    svc = FakeService()
    request = FakeRequest(
        outcomes=[ServerNotFoundError("Unable to resolve host 'www.googleapis.com'")]
    )
    svc.queue = [request]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append, max_retries=3)
    with pytest.raises(ApiError) as exc:
        client.account()
    assert exc.value.reason == "apiError"
    assert exc.value.status is None
    assert request.executions == 4
    assert sleeps == [1.0, 2.0, 4.0]
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert "www.googleapis.com" not in str(exc.value)
    assert "www.googleapis.com" not in tb


def test_refresh_error_immediate_sanitized():
    svc = FakeService()
    err = RefreshError(
        "Failed to refresh token for https://oauth2.googleapis.com/token "
        "(token: refresh-token-secret)"
    )
    svc.queue = [FakeRequest(error=err)]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append)
    with pytest.raises(ApiError) as exc:
        client.account()
    assert exc.value.status == 401
    assert exc.value.reason == "authError"
    assert sleeps == []
    assert len(svc.calls) == 1
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert "refresh-token-secret" not in str(exc.value)
    assert "refresh-token-secret" not in tb
    assert "oauth2.googleapis.com" not in tb


def test_exhausted_retries_raise():
    svc = FakeService()
    request = FakeRequest(outcomes=[http_error(500)])
    svc.queue = [request]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append, max_retries=3)
    with pytest.raises(ApiError) as exc:
        client.account()
    assert exc.value.status == 500
    assert len(svc.calls) == 1
    assert sleeps == [1.0, 2.0, 4.0]
    assert request.executions == 4


def test_repeated_page_token_raises_incomplete():
    svc = FakeService()
    svc.queue = [
        FakeRequest({"items": [playlist_raw("PL1", "A")], "nextPageToken": "t1"}),
        FakeRequest({"items": [playlist_raw("PL2", "B")], "nextPageToken": "t1"}),
    ]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).list_playlists()
    assert exc.value.reason == "incompleteSnapshot"


def test_api_error_exposes_status_and_reason():
    err = ApiError("boom", status=429, reason="quotaExceeded")
    assert err.status == 429
    assert err.reason == "quotaExceeded"
    assert str(err) == "boom"


def test_quota_on_403_preserves_quota_reason():
    svc = FakeService()
    svc.queue = [FakeRequest(error=http_error(403, "quotaExceeded"))]
    sleeps = []
    client = YouTubeClient(svc, sleep=sleeps.append)
    with pytest.raises(ApiError) as exc:
        client.account()
    assert exc.value.reason == "quotaExceeded"
    assert exc.value.status == 403
    assert sleeps == []
    assert len(svc.calls) == 1


def test_account_missing_id_raises_incomplete():
    svc = FakeService()
    svc.queue = [FakeRequest({"items": [{"snippet": {"title": "Me"}}]})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).account()
    assert exc.value.reason == "incompleteSnapshot"


def test_playlist_missing_id_raises_incomplete():
    svc = FakeService()
    raw = playlist_raw("PL1", "A")
    del raw["id"]
    svc.queue = [FakeRequest({"items": [raw]})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).list_playlists()
    assert exc.value.reason == "incompleteSnapshot"


def test_item_missing_id_raises_incomplete():
    svc = FakeService()
    raw = item_raw("i1", "v1")
    del raw["id"]
    svc.queue = [FakeRequest({"items": [raw]})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).list_playlist_items("PL")
    assert exc.value.reason == "incompleteSnapshot"


def test_malformed_response_raises_incomplete():
    svc = FakeService()
    svc.queue = [FakeRequest("not a dict")]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).account()
    assert exc.value.reason == "incompleteSnapshot"


def test_response_missing_items_raises_incomplete():
    svc = FakeService()
    svc.queue = [FakeRequest({"foo": "bar"})]
    with pytest.raises(ApiError) as exc:
        YouTubeClient(svc).account()
    assert exc.value.reason == "incompleteSnapshot"


def test_sanitized_error_hides_unknown_raw_reason():
    svc = FakeService()
    svc.queue = [FakeRequest(error=http_error(500))]
    client = YouTubeClient(svc, sleep=lambda *_: None)
    with pytest.raises(ApiError) as exc:
        client.account()
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert "someUnknownReason" not in str(exc.value)
    assert "someUnknownReason" not in tb


def test_sanitized_error_hides_url_secret():
    secret = "AIzaSy_supersecret"
    uri = f"https://www.googleapis.com/youtube/v3/channels?key={secret}"
    err = HttpError(
        SimpleNamespace(status=503, reason="Service Unavailable"),
        b'{"error": {"errors": [{"reason": "someUnknownReason"}]}}',
        uri=uri,
    )
    svc = FakeService()
    svc.queue = [FakeRequest(error=err)]
    client = YouTubeClient(svc, sleep=lambda *_: None)
    with pytest.raises(ApiError) as exc:
        client.account()
    tb = "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    assert secret not in str(exc.value)
    assert secret not in tb
