# Spot Delivery

Spot Delivery builds broadcast deliverables from source media using FFmpeg.

Status checkpoint: see `STATUS.md` for the current deployment/features snapshot.

## What It Does

- Adds timing structure around a spot: black + slate + black + spot + black
- Renders slate fields: Client header, Name, ISCI, Job#, Date, Length, Audio
- Exports ProRes 422 `.mov` outputs by profile
- Can generate one or two deliverables in one run
- Includes a Profile Builder UI to create/update/delete deliverable templates
- Includes Slate Preview to render a quick preview frame before full output
- Supports source selection by file upload/drag-and-drop in the web UI (works on Linux servers)
- Provides one-click download links in the web UI for newly rendered deliverables
- Includes a Slate Library page to manage dropdown choices for slate backgrounds
- Output filenames preserve the input stem (for example `ZACK12345H.MXF` -> `ZACK12345H.mov`), with extension driven by profile settings

## Run (Recommended Local Workflow)

```bash
./scripts/start_local.sh
```

Open: `http://127.0.0.1:3040`

Profile Builder: `http://127.0.0.1:3040/profiles`
Slate Library: `http://127.0.0.1:3040/slates`

Useful commands:

```bash
./scripts/status_local.sh
./scripts/stop_local.sh
./scripts/restart_local.sh
```

`start_local.sh` now runs in the foreground on purpose (no background/persistence behavior).

## Run (Docker)

```bash
docker compose up -d --build
```

Open: `http://localhost:3040`

## Current Profiles

Profiles are in `config/profiles.json`.

- `comcast_strata`
- `online_master`

Each profile controls timing, raster, frame rate, and output codecs.
Profiles can optionally keep the incoming/source frame rate instead of forcing a fixed profile FPS.
Each profile also controls output file extension (for example `.mov` or `.mpg`).

Profile APIs:

- `GET /api/profiles`
- `POST /api/profiles/save`
- `POST /api/profiles/delete`
- `POST /api/preview-slate`
- `POST /api/upload-source` (multipart form with `file`)
- `POST /api/upload-slate-background` (multipart form with `file`)
- `GET /api/slates` (list slate background choices)
- `POST /api/slates/delete` (remove one slate by path)
- `GET /api/download/<token>` (tokenized download URL returned by `/api/render`)

## Notes

- Requires `ffmpeg` + `ffprobe` (included in Docker image)
- Slate text requires FFmpeg `drawtext`; if unavailable, renders still succeed but slate text is skipped with a warning
- On macOS/Homebrew, the app prefers `ffmpeg-full` automatically when installed (`/opt/homebrew/opt/ffmpeg-full/bin`)
- Optional overrides: `SPOT_DELIVERY_FFMPEG_BIN` and `SPOT_DELIVERY_FFPROBE_BIN`
- Automatic retention cleanup deletes files older than 14 days from `uploads/source`, `uploads/slate`, and `outputs` (override with `SPOT_DELIVERY_RETENTION_DAYS`)
- Input file path must be readable from where the app runs
- Outputs are written to `./outputs` by default
- `launchd` persistence is blocked by default and requires explicit opt-in:
  `SPOT_DELIVERY_ALLOW_PERSISTENCE=1 ./scripts/install_launchd_service.sh`

## Quick System Check

Run while the app is up:

```bash
curl -s http://127.0.0.1:3040/api/system-check
```
