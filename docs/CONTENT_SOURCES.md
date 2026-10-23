# Content Sources

This document defines the supported sources for evidence-backed content claims.
Metadata-only organization suggestions must be labeled as suggestions; they are
not verified content facts or evidence-grounded recipe/category analyses.

## Summary

- Evidence is **local and user-supplied** only.
- The manager **never** scrapes YouTube videos or downloads captions on its own.
- There is **no automatic frame/video analysis**.
- No recipe, category, or tag is ever invented from a video title or metadata.

## Supported evidence kinds

| Kind | Description |
|---|---|
| `transcript` | Spoken-word transcript imported from a local `.txt`, `.srt`, or `.vtt` file. |
| `visual_notes` | User-authored observations about what is shown on screen. |
| `notes` | Free-form user notes about the video's content. |

All three are imported via the local **content import** path and are bound to a
specific, already-known `video_id` in the inventory.

## What is *not* supported

- **Automatic YouTube video download** of any kind.
- **Automatic caption scraping** from YouTube.
- **Third-party transcript/download access** — this is not provided.

### YouTube captions are not a transcript source

The YouTube Data API v3 `captions.download` endpoint **requires edit permission
and the write scope**, and it does **not** grant access to third-party
transcripts. It is therefore not used as a content source here.

- Official reference — captions.download:
  https://developers.google.com/youtube/v3/docs/captions/download
- Official reference — captions.list:
  https://developers.google.com/youtube/v3/docs/captions/list

## Visual sources

The only legal visual sources are:

- **User-owned or user-licensed** video observation notes.
- **User-supplied** notes.

There is **no automatic frame extraction or video analysis** claimed by this
tool.

## Analysis runtime

Prepare analysis JSON manually or with a conversational agent, then use
`analysis import` to validate it against stored evidence and save it locally.
Use `analysis list` and `analysis categories` to inspect results and proposals.
Every fact still requires an exact quote from the cited evidence.

The project includes no model-server integration. Import and validation make no
model-service requests. Sharing private evidence with an external agent requires
the user's separate, explicit consent.
