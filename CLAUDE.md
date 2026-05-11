# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Liked Songs Downloader** — Flask web app for downloading liked Spotify tracks as audio files without Spotify Premium. No OAuth required; resolves tracks via public Spotify pages + YouTube search + yt-dlp.

Two-phase workflow:
1. **Setup**: Paste raw Spotify URLs or plain text song descriptions → app resolves tracks
2. **Sync**: Download resolved tracks to local folder with parallelized yt-dlp + FFmpeg audio extraction

## Architecture

### Core Components

**app.py** (main Flask application, ~1050 lines)

- `YoutubeSearchCache` — Thread-safe cache of resolved YouTube videos (persisted to `data/youtube_cache.json`)
- `State` — Thread-safe persistence layer for completed/failed downloads with retry tracking
- `SyncManager` — Coordinates download progress, emits events to UI subscribers (status/log events)

**Track Resolution**
- `resolve_spotify_track()` — Scrapes public Spotify track pages (OG meta tags) for title/artists (no auth)
- `extract_track_id()` — Regex parser for Spotify URLs & URIs
- `parse_input_lines()` — Splits pasted text into Spotify track IDs vs plain text queries

**YouTube Search & Ranking**
- `yt_search()` — Primary search via `yt-dlp` query
- `yt_search_invidious()` — Fallback search via Invidious API (privacy-focused proxy)
- `yt_search_flat()` — Flat JSON query parser (internal)
- `score_match()` — Ranks videos by (duration ≈ Spotify metadata, title substring match, upload recency)
- `find_best()` — Finds best-scoring video; retries name-only if artist match fails

**Download & Extraction**
- `download_audio()` — Uses `yt-dlp` with FFmpeg postprocessor to extract audio as MP3/OGG/OPUS
- `download_audio_invidious()` — Fallback download via Invidious streaming URL (handles video-only sources)
- `process_track()` — Single-track pipeline: search → download → move to dest folder → mark completed

**Sync Orchestration**
- `run_resolve()` — Background thread: resolves Spotify URLs + plain text queries in parallel
- `run_sync()` — Background thread: downloads all resolved songs with ThreadPoolExecutor (default 10 workers)
- Event emission: both threads emit `status` and `log` events via `SyncManager` → Server-Sent Events → UI

**Flask Routes** (`/api/...`)
- POST `/api/config` — Save download folder path
- POST `/api/songs/load` — Parse pasted input + trigger `run_resolve()` in background
- GET `/api/songs/list` — Return loaded songs (with resolve state progress)
- POST `/api/songs/clear` — Clear all songs
- POST `/api/songs/verify` — Validate Spotify URLs before adding
- GET/POST `/api/resolve/status` — Resolve thread progress
- POST `/api/sync/start` — Trigger download phase (`run_sync()`)
- GET `/api/sync/status` — Download progress snapshot
- GET `/api/sync/stream` — SSE stream of live status/log events during download
- POST `/api/sync/stop` — Signal download thread to stop gracefully
- POST `/api/state/reset-failed` — Clear failed state (for retries)
- POST `/api/data/clear` — Nuke all persistent data

**Templates & Static**
- `setup.html` — Input wizard: paste URLs/songs → preview → save to songs list
- `dashboard.html` — Download progress: live status, per-track log, result summary
- `style.css` — Responsive UI (wizard + dashboard views)
- `spotify-scraper.js` — Bookmarklet for scraping Liked Songs directly from Spotify web UI (included for reference, not used by app)

### Data Files (in `data/` directory)

- `config.json` — User config (download folder path, audio format/quality)
- `songs.json` — List of resolved tracks (id, name, artists, duration_ms)
- `state.json` — Persistent sync state (completed/failed tracks with timestamps/error details)
- `youtube_cache.json` — YouTube video cache (artist|name → video id/title/duration/URL)
- `tmp/` — Temporary download staging area (cleaned after move)

## How to Run

```bash
# Install FFmpeg (required for audio extraction)
brew install ffmpeg       # macOS
sudo apt install ffmpeg   # Linux

# Create & activate virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the app (opens http://127.0.0.1:8080 in default browser)
python app.py
```

## Development Notes

### Threading Model

- Main thread: Flask request handlers → emit API responses
- `run_resolve()` thread: Spawned on `/api/songs/load` POST; resolves Spotify URLs in parallel
- `run_sync()` thread: Spawned on `/api/sync/start` POST; downloads with ThreadPoolExecutor (10 workers default)
- Event subscribers: UI polls `/api/sync/stream` (SSE) for live log/status events

All state mutations (song lists, completed/failed) protected by thread locks.

### Key Invariants

- Spotify track IDs are unique (deduped in `parse_input_lines()`)
- Downloaded files are atomic: write to tmp, then move to dest (prevents partial files)
- Completed state is persistent: restarting doesn't re-download
- Failed tracks retry on next sync (unless `reset_failed` called)

### Testing

No test suite yet. Verify via:
- Manual: run app, paste Spotify URL, click sync, check ~/Downloads/Liked\ Songs/
- Edge cases:
  - Duplicate Spotify URLs (should dedupe)
  - Unavailable videos (should mark failed)
  - Network failures (should retry with backoff)
  - Long lists (10k songs) — check ThreadPoolExecutor performance

### Common Edits

- **Change download folder**: Modify `DEFAULT_FOLDER` constant
- **Tune YouTube ranking**: Edit `score_match()` weights
- **Adjust parallelism**: Change `ThreadPoolExecutor(max_workers=10)` in `run_sync()`
- **Add retry backoff**: Modify exception handling in `download_audio()`
- **New audio codec**: Add postprocessor to `yt_dlp` opts in `download_audio()`

### Debugging Tips

- Check console logs: `python app.py` shows [INFO]/[WARNING] to stdout
- Check UI logs: `/api/sync/stream` emits all download events in real-time
- State inspection: read `data/state.json` to see completed/failed tracks
- Cache debugging: clear `data/youtube_cache.json` to force new YouTube searches
- FFmpeg issues: run `ffmpeg -version` to verify installation

## Notes

- Readme is in Spanish (Spanish Spotify user context)
- No Spotify API key or auth required — all resolution via public pages
- Invidious fallback used only if yt-dlp search fails (privacy-respecting)
