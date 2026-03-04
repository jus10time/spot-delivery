# Spot Delivery Status

Last updated: 2026-03-04

## Current State

- Runtime: Flask app with FFmpeg render pipeline
- Deployment target: `productiondev` (`http://productiondev.am.com:3017`)
- Container: `spot-delivery` (Docker Compose, restart policy `unless-stopped`)
- Port mapping: host `3017` -> container/app `3040`

## Delivered Features

- Source media upload by click-select and drag/drop
- Deliverable profile builder (save/delete profiles)
- Slate rendering with profile-driven timing and layout
- Slate library management page (`/slates`) with upload/list/delete
- Slate background dropdown in delivery form
- Profile option to keep incoming/source frame rate (`keep_frame_rate`)
- Profile-controlled output extension (`output_extension`) so output stem matches source stem
  - Example: `ZACK12345H.MXF` -> `ZACK12345H.mov` or `ZACK12345H.mpg`
- Direct deliverable download links in web UI after render
- Automatic media cleanup retention window (default 14 days)
  - Applies to `uploads/source`, `uploads/slate`, and `outputs`
  - Controlled by `SPOT_DELIVERY_RETENTION_DAYS`

## Operational Notes

- Browser file picker issues tied to OS-level `osascript` are bypassed with HTML file input and drag/drop.
- Form fields reset on page reload to avoid stale unusable paths.
- Selected slate choice persists across reload via browser localStorage.
- Uploaded files persist across normal app/container restart when `uploads` mount is preserved.

## APIs In Use

- `GET /api/profiles`
- `POST /api/profiles/save`
- `POST /api/profiles/delete`
- `POST /api/upload-source`
- `POST /api/upload-slate-background`
- `GET /api/slates`
- `POST /api/slates/delete`
- `POST /api/preview-slate`
- `POST /api/render`
- `GET /api/download/<token>`

## Immediate Follow-Up

- Keep deployment sync from deleting `uploads/` content.
- Add periodic checks for available disk usage as output volume grows.
