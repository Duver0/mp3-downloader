"""
Liked Songs Downloader — local web app (no Spotify Premium needed)
Run: python app.py  → opens http://127.0.0.1:8080
"""
from __future__ import annotations

import html as html_module
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yt_dlp
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

# ─── Paths & constants ──────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
SONGS_PATH = DATA_DIR / "songs.json"
TMP_DOWNLOAD_DIR = DATA_DIR / "tmp"

PORT = 8080
HOST = "127.0.0.1"
DEFAULT_FOLDER = str(Path.home() / "Downloads" / "Liked Songs")

DATA_DIR.mkdir(exist_ok=True)
TMP_DOWNLOAD_DIR.mkdir(exist_ok=True)

# ─── Logger ─────────────────────────────────────────────────────────────────
log = logging.getLogger("lsd")
log.setLevel(logging.INFO)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                  datefmt="%H:%M:%S"))
log.addHandler(_h)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ─── YouTube Search Cache ──────────────────────────────────────────────────
class YoutubeSearchCache:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning(f"cache corrupted, starting fresh: {self.path}")
                return {}
        return {}

    def _save_unlocked(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    def get(self, artist: str, name: str) -> Optional[dict]:
        key = f"{artist}|{name}".strip("|")
        with self._lock:
            return self._data.get(key)

    def set(self, artist: str, name: str, video: dict) -> None:
        key = f"{artist}|{name}".strip("|")
        with self._lock:
            self._data[key] = {
                "id": video.get("id"),
                "title": video.get("title"),
                "duration": video.get("duration"),
                "url": video.get("url"),
            }
            self._save_unlocked()


# ─── Config persistence ─────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError: pass
    return {}

def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")

def load_songs() -> list[dict]:
    if SONGS_PATH.exists():
        try: return json.loads(SONGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError: pass
    return []

def save_songs(songs: list[dict]) -> None:
    SONGS_PATH.write_text(json.dumps(songs, indent=2, ensure_ascii=False),
                          encoding="utf-8")

def is_setup_complete() -> bool:
    cfg = load_config()
    return bool(cfg.get("download_folder")) and len(load_songs()) > 0


# ─── State (resumable) ──────────────────────────────────────────────────────
class State:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                d.setdefault("completed", {})
                d.setdefault("failed", {})
                return d
            except json.JSONDecodeError: pass
        return {"completed": {}, "failed": {}}

    def _save_unlocked(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    @property
    def completed(self):
        with self._lock: return dict(self._data["completed"])
    @property
    def failed(self):
        with self._lock: return dict(self._data["failed"])
    def is_completed(self, tid):
        with self._lock: return tid in self._data["completed"]
    def mark_completed(self, tid, filename):
        with self._lock:
            self._data["completed"][tid] = {
                "filename": filename,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            self._data["failed"].pop(tid, None)
            self._save_unlocked()
    def mark_failed(self, tid, error):
        with self._lock:
            existing = self._data["failed"].get(tid, {})
            self._data["failed"][tid] = {
                "error": error,
                "attempts": existing.get("attempts", 0) + 1,
                "last_attempt": datetime.now(timezone.utc).isoformat(),
            }
            self._save_unlocked()
    def reset_failed(self):
        with self._lock:
            self._data["failed"] = {}
            self._save_unlocked()
    def reset_all(self):
        with self._lock:
            self._data = {"completed": {}, "failed": {}}
            self._save_unlocked()

state = State(STATE_PATH)
youtube_cache = YoutubeSearchCache(DATA_DIR / "youtube_cache.json")


# ─── Sync orchestrator ──────────────────────────────────────────────────────
class SyncManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.is_running = False
        self.should_stop = False
        self.total = 0
        self.processed = 0
        self.ok = 0
        self.failed = 0
        self.skipped = 0
        self.current = ""
        self.last_error: Optional[str] = None
        self.subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "is_running": self.is_running,
                "total": self.total, "processed": self.processed,
                "ok": self.ok, "failed": self.failed, "skipped": self.skipped,
                "current": self.current, "last_error": self.last_error,
                "completed_total": len(state.completed),
                "failed_total": len(state.failed),
                "songs_total": len(load_songs()),
            }

    def subscribe(self) -> queue.Queue:
        q = queue.Queue()
        with self._sub_lock: self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self.subscribers: self.subscribers.remove(q)

    def emit(self, event_type: str, payload: dict):
        evt = {"type": event_type, "ts": time.time(), **payload}
        with self._sub_lock:
            for q in list(self.subscribers):
                try: q.put_nowait(evt)
                except queue.Full: pass

    def emit_log(self, message: str, level: str = "info"):
        self.emit("log", {"message": message, "level": level})

    def emit_status(self):
        self.emit("status", self.snapshot())

    def increment_ok(self):
        with self._lock: self.ok += 1
    def increment_failed(self):
        with self._lock: self.failed += 1
    def increment_skipped(self):
        with self._lock: self.skipped += 1
    def add_processed(self, n: int):
        with self._lock: self.processed += n
    def set_current(self, label: str):
        with self._lock: self.current = label

sync = SyncManager()


# ─── Spotify track resolver (no API, just public pages) ─────────────────────
SPOTIFY_TRACK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)([a-zA-Z0-9]+)")
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_track_id(text: str) -> Optional[str]:
    m = SPOTIFY_TRACK_RE.search(text)
    return m.group(1) if m else None


def resolve_spotify_track(track_id: str, session: requests.Session) -> Optional[dict]:
    """Read the public Spotify track page and parse OG meta tags. No auth needed."""
    url = f"https://open.spotify.com/track/{track_id}"
    try:
        r = session.get(url, timeout=10, allow_redirects=True)
        if not r.ok:
            return None
        html = r.text
        title_m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        desc_m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if not title_m:
            return None
        title = html_module.unescape(title_m.group(1))
        desc = html_module.unescape(desc_m.group(1)) if desc_m else ""
        # description format examples:
        #   "Artist Name · Song · 2023"
        #   "Artist1, Artist2 · Song · 2023"
        artist = ""
        if "·" in desc:
            artist = desc.split("·")[0].strip()
        return {
            "id": track_id,
            "name": title,
            "artists": [a.strip() for a in artist.split(",")] if artist else [],
            "duration_ms": 0,  # not available without API; YouTube ranking copes
        }
    except requests.RequestException:
        return None


def parse_input_lines(text: str) -> tuple[list[str], list[str]]:
    """Split a pasted blob into (spotify_track_ids, plain_text_queries)."""
    track_ids: list[str] = []
    plain: list[str] = []
    seen_ids: set[str] = set()
    seen_plain: set[str] = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        tid = extract_track_id(line)
        if tid:
            if tid not in seen_ids:
                seen_ids.add(tid)
                track_ids.append(tid)
        else:
            key = line.lower()
            if key not in seen_plain:
                seen_plain.add(key)
                plain.append(line)
    return track_ids, plain


def parse_input_lines_verbose(text: str) -> tuple[list[str], list[str], dict]:
    """Same as parse_input_lines but returns stats about parsing."""
    track_ids: list[str] = []
    plain: list[str] = []
    seen_ids: set[str] = set()
    seen_plain: set[str] = set()
    stats = {"total": 0, "track_ids": 0, "plain": 0, "empty": 0, "lines": []}

    for raw in text.splitlines():
        stats["total"] += 1
        line = raw.strip()
        if not line:
            stats["empty"] += 1
            continue
        tid = extract_track_id(line)
        if tid:
            if tid not in seen_ids:
                seen_ids.add(tid)
                track_ids.append(tid)
                stats["track_ids"] += 1
        else:
            key = line.lower()
            stats["lines"].append({"raw": raw, "parsed": line})
            if key not in seen_plain:
                seen_plain.add(key)
                plain.append(line)
                stats["plain"] += 1
    return track_ids, plain, stats


# ─── Pipeline helpers ───────────────────────────────────────────────────────
def sanitize(name: str, max_len: int = 180) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name: return "untitled"
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.match(r"^(COM|LPT)\d$", name.upper()):
        name = "_" + name
    
    encoded = name.encode('utf-8')
    if len(encoded) > max_len:
        while len(name) > 1 and len(name.encode('utf-8')) > max_len:
            name = name[:-1]
    
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.match(r"^(COM|LPT)\d$", name.upper()):
        name = "_" + name
    return name


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Sanitize for filename use - only song name."""
    return sanitize(name, max_len)


def yt_search(query: str, n: int = 5) -> list[dict]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True, "default_search": "ytsearch"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
            return res.get("entries", []) or []
    except Exception:
        return []


def yt_search_invidious(query: str, n: int = 5) -> list[dict]:
    """Search via Invidious instance (bypasses age restrictions)."""
    invidious_instances = [
        "https://inv.nadeko.net",
        "https://invidious.privacyredirect.com",
        "https://yewtu.be",
    ]
    for instance in invidious_instances:
        try:
            search_url = f"{instance}/search?q={query}"
            opts = {
                "quiet": True, "no_warnings": True,
                "skip_download": True,
                "extractor_keys": ["Invidious"],
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                res = ydl.extract_info(search_url, download=False)
                if res and res.get("entries"):
                    return res["entries"][:n]
        except Exception:
            continue
    return []


def yt_search_flat(query: str, n: int = 5) -> list[dict]:
    """Try without 'ytsearch' prefix - get flat results."""
    opts = {
        "quiet": True, "no_warnings": True,
        "skip_download": True,
        "default_search": query,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(query, download=False)
            if not res:
                return []
            entries = res.get("entries", []) or []
            return entries[:n]
    except Exception:
        return []


def score_match(video: dict, track: dict) -> float:
    s = 0.0
    title = (video.get("title") or "").lower()
    name = track["name"].lower()
    artist = track["artists"][0].lower() if track["artists"] else ""

    if name in title: s += 30
    elif all(w in title for w in name.split() if len(w) > 2): s += 15
    if artist and artist in title: s += 25

    yt_dur = video.get("duration")
    sp_dur = track.get("duration_ms", 0) / 1000 if track.get("duration_ms") else None
    if yt_dur and sp_dur:
        d = abs(yt_dur - sp_dur)
        if d < 3: s += 40
        elif d < 10: s += 25
        elif d < 30: s += 5
        elif d > 90: s -= 30

    if "official audio" in title or "official video" in title: s += 10
    elif "lyric" in title: s += 5

    if "cover" in title and "cover" not in name: s -= 20
    if "remix" in title and "remix" not in name: s -= 15
    if "live" in title and "live" not in name: s -= 10
    if "reaction" in title or "review" in title: s -= 50
    if ("karaoke" in title or "instrumental" in title) and \
       "karaoke" not in name and "instrumental" not in name: s -= 30
    if "8d audio" in title or "slowed" in title or "sped up" in title: s -= 25
    return s


def find_best(track: dict, retry_name_only: bool = False) -> Optional[dict]:
    artist = track["artists"][0] if track["artists"] else ""
    name = track["name"]

    # Check cache first (before search)
    if not retry_name_only and artist and name:
        cached = youtube_cache.get(artist, name)
        if cached:
            return cached

    queries = []
    if not retry_name_only and artist and name:
        queries.append(f"{artist} {name}".strip())
    if name:
        queries.append(name)
    if not retry_name_only and name and artist:
        queries.append(f"{artist} {name} audio".strip())
    if name:
        queries.append(f"{name} audio".strip())
    if name:
        queries.append(f"{name} official audio".strip())

    seen, candidates = set(), []
    for q in queries:
        if not q: continue
        if retry_name_only:
            for c in yt_search_flat(q, n=5):
                cid = c.get("id")
                if cid and cid not in seen:
                    seen.add(cid); candidates.append(c)
            if not candidates:
                for c in yt_search_invidious(q, n=5):
                    cid = c.get("id")
                    if cid and cid not in seen:
                        seen.add(cid); candidates.append(c)
        else:
            for c in yt_search(q, n=5):
                cid = c.get("id")
                if cid and cid not in seen:
                    seen.add(cid); candidates.append(c)
        if len(candidates) >= 5: break
    if not candidates: return None
    scored = sorted(((score_match(c, track), c) for c in candidates),
                    key=lambda x: x[0], reverse=True)
    best_s, best = scored[0]
    result = best if best_s >= 20 else None

    # Store in cache if found
    if result and not retry_name_only and artist and name:
        youtube_cache.set(artist, name, result)

    return result


def download_audio(video: dict, out_no_ext: Path,
                   audio_format: str, audio_quality: str) -> Path:
    vid = video.get("id")
    url = video.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
    if not url: raise ValueError("video sin URL")
    
    final = out_no_ext.with_suffix(f".{audio_format}")
    
    if final.exists():
        return final
    
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_no_ext) + ".%(ext)s",
        "quiet": True, "no_warnings": True, "noprogress": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": audio_quality,
        }],
        "retries": 3, "fragment_retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        if "age" in str(e).lower() or "sign in" in str(e).lower():
            return download_audio_invidious(vid, out_no_ext, audio_format, audio_quality)
        raise
    
    for ext in [audio_format, "webm", "m4a", "mp4", "ogg", "opus", "flac"]:
        check = out_no_ext.with_suffix(f".{ext}")
        if check.exists():
            if ext != audio_format:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(check),
                    "-acodec", "libmp3lame" if audio_format == "mp3" else "aac",
                    "-q:a", "2", str(final)
                ], capture_output=True)
                check.unlink()
                return final
            else:
                return final
    
    files_in_tmp = list(TMP_DOWNLOAD_DIR.glob(f"{out_no_ext.name}.*"))
    if files_in_tmp:
        result_file = files_in_tmp[0]
        if result_file.suffix.lstrip('.') != audio_format:
            subprocess.run([
                "ffmpeg", "-y", "-i", str(result_file),
                "-acodec", "libmp3lame" if audio_format == "mp3" else "aac",
                "-q:a", "2", str(final)
            ], capture_output=True)
            result_file.unlink()
        else:
            result_file.rename(final)
        return final
    
    raise FileNotFoundError(f"archivo no encontrado: {final}")


def download_audio_invidious(video_id: str, out_no_ext: Path,
                             audio_format: str, audio_quality: str) -> Path:
    """Download via Invidious API (bypasses age restrictions)."""
    invidious_instances = [
        "https://inv.nadeko.net",
        "https://invidious.privacyredirect.com",
        "https://yewtu.be",
    ]
    
    for instance in invidious_instances:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            r = requests.get(api_url, timeout=10)
            if not r.ok: continue
            
            data = r.json()
            streaming_url = None
            for format_data in data.get("streamingData", {}).get("adaptiveFormats", []):
                if "audio" in format_data.get("type", "") or format_data.get("audioQuality"):
                    streaming_url = format_data.get("url")
                    if streaming_url: break
            
            if not streaming_url:
                for format_data in data.get("streamingData", {}).get("formats", []):
                    if "audio" in format_data.get("type", ""):
                        streaming_url = format_data.get("url")
                        if streaming_url: break
            
            if not streaming_url: continue
            
            opts = {
                "format": "bestaudio/best",
                "outtmpl": str(out_no_ext) + ".%(ext)s",
                "quiet": True, "no_warnings": True, "noprogress": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": audio_quality,
                }],
                "retries": 3,
            }
            opts["external_downloader"] = "stream"
            opts["hls_use_mpegts"] = True
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([streaming_url])
            
            final = out_no_ext.with_suffix(f".{audio_format}")
            if final.exists(): return final
        except Exception:
            continue
    
    raise ValueError(f"no se pudo descargar via Invidious: {video_id}")


def process_track(track: dict, dest_dir: Path, audio_format: str, audio_quality: str) -> str:
    tid = track["id"]
    name = track["name"]
    artist = track["artists"][0] if track["artists"] else ""
    label = f"{artist} - {name}".strip(" -")
    if state.is_completed(tid):
        return f"SKIP {label}"

    sync.current = label
    sync.emit_status()

    video = find_best(track)
    if not video:
        if track["artists"] and track["artists"][0]:
            sync.emit_log(f"  ↻ reintentando solo con nombre...", "info")
            video = find_best(track, retry_name_only=True)
    
    if not video:
        state.mark_failed(tid, "no_match_found")
        return f"FAIL [search] {label}"

    safe = sanitize(name)
    tmp_no_ext = TMP_DOWNLOAD_DIR / safe
    final_dest = dest_dir / f"{safe}.{audio_format}"

    try:
        tmp_final = download_audio(video, tmp_no_ext, audio_format, audio_quality)
    except Exception as e:
        for ext in (audio_format, "webm", "m4a", "mp4", "opus", "part"):
            p = tmp_no_ext.with_suffix(f".{ext}")
            if p.exists():
                try: p.unlink()
                except OSError: pass
        state.mark_failed(tid, f"download_error: {e}")
        return f"FAIL [download] {label}: {e}"

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if final_dest.exists(): final_dest.unlink()
        shutil.move(str(tmp_final), str(final_dest))
    except Exception as e:
        state.mark_failed(tid, f"move_error: {e}")
        return f"FAIL [save] {label}: {e}"

    state.mark_completed(tid, f"{safe}.{audio_format}")
    return f"OK {label}"


# ─── Resolve thread (background) ────────────────────────────────────────────
resolve_state = {
    "is_running": False, "total": 0, "done": 0,
    "ok": 0, "failed": 0, "error": None,
}
resolve_lock = threading.Lock()


def run_resolve(text: str):
    with resolve_lock:
        if resolve_state["is_running"]:
            return
        resolve_state.update({"is_running": True, "total": 0, "done": 0,
                              "ok": 0, "failed": 0, "error": None})

    try:
        track_ids, plain = parse_input_lines(text)
        total = len(track_ids) + len(plain)
        with resolve_lock: resolve_state["total"] = total

        sync.emit_log(f"📋 Procesando lista: {len(track_ids)} URLs de Spotify + {len(plain)} líneas de texto", "info")
        sync.emit("resolve", dict(resolve_state))

        existing = {s["id"]: s for s in load_songs()}
        new_songs = list(existing.values())

        # Resolve plain text immediately (no network)
        for text_line in plain:
            tid = "txt:" + re.sub(r"\W+", "_", text_line.lower())[:100]
            if tid not in existing:
                # naive split by " - " for title/artist
                parts = [p.strip() for p in text_line.split(" - ", 1)]
                if len(parts) == 2:
                    name, artist = parts
                else:
                    name, artist = text_line, ""
                new_songs.append({
                    "id": tid, "name": name,
                    "artists": [artist] if artist else [],
                    "duration_ms": 0, "source": "text",
                })
            with resolve_lock:
                resolve_state["done"] += 1; resolve_state["ok"] += 1
            sync.emit("resolve", dict(resolve_state))

        # Resolve Spotify URLs in parallel
        sess = requests.Session()
        sess.headers.update(HTTP_HEADERS)

        def resolve_one(tid):
            if tid in existing:
                return tid, existing[tid], None
            track = resolve_spotify_track(tid, sess)
            return tid, track, None if track else "no metadata"

        if track_ids:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = [ex.submit(resolve_one, tid) for tid in track_ids]
                for fut in as_completed(futures):
                    tid, track, err = fut.result()
                    if track:
                        if not any(s["id"] == tid for s in new_songs):
                            track["source"] = "spotify"
                            new_songs.append(track)
                        with resolve_lock:
                            resolve_state["done"] += 1; resolve_state["ok"] += 1
                    else:
                        with resolve_lock:
                            resolve_state["done"] += 1; resolve_state["failed"] += 1
                        sync.emit_log(f"  ⚠ no pude leer metadata de {tid}", "warn")
                    sync.emit("resolve", dict(resolve_state))

        save_songs(new_songs)
        sync.emit_log(f"✅ Lista cargada: {len(new_songs)} canciones totales", "success")
    except Exception as e:
        with resolve_lock: resolve_state["error"] = str(e)
        sync.emit_log(f"❌ Error procesando lista: {e}", "error")
        log.exception("resolve error")
    finally:
        with resolve_lock: resolve_state["is_running"] = False
        sync.emit("resolve_done", dict(resolve_state))


# ─── Sync thread ────────────────────────────────────────────────────────────
def run_sync(retry_failed: bool = False):
    cfg = load_config()
    dest = Path(cfg.get("download_folder") or DEFAULT_FOLDER).expanduser()
    audio_format = cfg.get("audio_format", "mp3")
    audio_quality = cfg.get("audio_quality", "192")

    try:
        sync.is_running = True
        sync.should_stop = False
        sync.processed = sync.ok = sync.failed = sync.skipped = 0
        sync.last_error = None

        all_songs = load_songs()
        sync.emit_log(f"📥 Lista cargada: {len(all_songs)} canciones", "info")

        if retry_failed:
            failed_ids = set(state.failed.keys())
            songs = [s for s in all_songs if s["id"] in failed_ids]
            sync.emit_log(f"   Reintentando {len(songs)} fallidas", "info")
        else:
            songs = [s for s in all_songs if not state.is_completed(s["id"])]
            sync.emit_log(f"   Pendientes: {len(songs)}", "info")

        sync.total = len(songs)
        sync.emit_status()

        if not songs:
            sync.emit_log("✅ Nada que hacer. Todo descargado.", "success")
            return

        sync.emit_log(f"🚀 Descargando a: {dest}", "info")

        def process_one(track):
            if sync.should_stop:
                return None
            try:
                return process_track(track, dest, audio_format, audio_quality)
            except Exception as e:
                result = f"FAIL [unexpected] {track.get('name','?')}: {e}"
                state.mark_failed(track["id"], f"unexpected: {e}")
                return result

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(process_one, track): track for track in songs}
            for future in as_completed(futures):
                if sync.should_stop:
                    sync.emit_log("⏹  Detenido por el usuario.", "warn")
                    break
                try:
                    result = future.result()
                except Exception as e:
                    track = futures[future]
                    result = f"FAIL [unexpected] {track.get('name','?')}: {e}"
                    state.mark_failed(track["id"], f"unexpected: {e}")

                if result is None:
                    continue

                if result.startswith("OK"):
                    sync.increment_ok()
                    sync.emit_log(f"  ✓ {result[3:]}", "success")
                elif result.startswith("SKIP"):
                    sync.increment_skipped()
                    sync.emit_log(f"  ↷ {result[5:]}", "muted")
                else:
                    sync.increment_failed()
                    sync.emit_log(f"  ✗ {result[5:]}", "error")

                with sync._lock:
                    sync.processed = sync.ok + sync.failed + sync.skipped
                sync.emit_status()

        sync.emit_log(f"⏱  Terminado: {sync.ok} ok / {sync.failed} fail / {sync.skipped} skip", "info")
    except Exception as e:
        sync.last_error = str(e)
        sync.emit_log(f"❌ Error: {e}", "error")
        log.exception("sync error")
    finally:
        sync.is_running = False
        sync.current = ""
        sync.emit_status()
        sync.emit("done", {})


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# ─── Flask app ──────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=str(APP_DIR / "templates"),
            static_folder=str(APP_DIR / "static"))


@app.route("/")
def root():
    if not is_setup_complete():
        return redirect(url_for("setup"))
    return redirect(url_for("dashboard"))


@app.route("/setup")
def setup():
    cfg = load_config()
    songs = load_songs()
    step = request.args.get("step", "1")
    return render_template(
        "setup.html",
        step=step, cfg=cfg,
        default_folder=DEFAULT_FOLDER,
        songs_count=len(songs),
        ffmpeg_ok=check_ffmpeg(),
    )


@app.route("/dashboard")
def dashboard():
    if not is_setup_complete():
        return redirect(url_for("setup"))
    cfg = load_config()
    return render_template(
        "dashboard.html",
        cfg=cfg,
        completed_total=len(state.completed),
        failed_total=len(state.failed),
        songs_total=len(load_songs()),
        ffmpeg_ok=check_ffmpeg(),
    )


@app.route("/api/config", methods=["POST"])
def api_save_config():
    body = request.get_json(force=True)
    cfg = load_config()
    if "download_folder" in body:
        folder = body["download_folder"].strip() or DEFAULT_FOLDER
        cfg["download_folder"] = str(Path(folder).expanduser())
    if "audio_format" in body:
        cfg["audio_format"] = body["audio_format"]
    if "audio_quality" in body:
        cfg["audio_quality"] = str(body["audio_quality"])
    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/songs/load", methods=["POST"])
def api_songs_load():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    if not text:
        return jsonify({"error": "No pegaste nada."}), 400
    with resolve_lock:
        if resolve_state["is_running"]:
            return jsonify({"error": "Ya hay una carga en progreso."}), 409
    threading.Thread(target=run_resolve, kwargs={"text": text}, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/songs/list")
def api_songs_list():
    return jsonify({"songs": load_songs()})


@app.route("/api/songs/clear", methods=["POST"])
def api_songs_clear():
    save_songs([])
    state.reset_all()
    return jsonify({"ok": True})


@app.route("/api/songs/verify", methods=["POST"])
def api_songs_verify():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    track_ids, plain, stats = parse_input_lines_verbose(text)
    existing = {s["id"]: s for s in load_songs()}
    
    result = {
        "input_stats": stats,
        "stored_count": len(existing),
        "stored_text_count": len([s for s in existing if s["id"].startswith("txt:")]),
        "new_from_input": len(plain),
        "likely_duplicates": stats["total"] - stats["empty"] - len(plain) - len(track_ids),
        "input_lines_sample": stats["lines"][:10] if stats["lines"] else [],
    }
    return jsonify(result)


@app.route("/api/resolve/status")
def api_resolve_status():
    with resolve_lock:
        return jsonify(dict(resolve_state))


@app.route("/api/sync/start", methods=["POST"])
def api_sync_start():
    if sync.is_running:
        return jsonify({"error": "ya hay una descarga en curso"}), 409
    if not check_ffmpeg():
        return jsonify({"error": "ffmpeg no está instalado"}), 400
    if len(load_songs()) == 0:
        return jsonify({"error": "no hay lista de canciones cargada"}), 400
    body = request.get_json(silent=True) or {}
    retry = bool(body.get("retry_failed", False))
    threading.Thread(target=run_sync, kwargs={"retry_failed": retry},
                     daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/sync/stop", methods=["POST"])
def api_sync_stop():
    sync.should_stop = True
    return jsonify({"ok": True})


@app.route("/api/sync/status")
def api_sync_status():
    return jsonify(sync.snapshot())


@app.route("/api/sync/stream")
def api_sync_stream():
    q = sync.subscribe()
    def gen():
        try:
            yield f"data: {json.dumps({'type': 'status', **sync.snapshot()})}\n\n"
            while True:
                try:
                    evt = q.get(timeout=15)
                    yield f"data: {json.dumps(evt)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            sync.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/api/state/reset-failed", methods=["POST"])
def api_reset_failed():
    state.reset_failed()
    return jsonify({"ok": True})


@app.route("/api/data/clear", methods=["POST"])
def api_data_clear():
    save_songs([])
    state.reset_all()
    cfg = load_config()
    folder = Path(cfg.get("download_folder") or DEFAULT_FOLDER).expanduser()
    if folder.exists() and folder.is_dir():
        for f in folder.glob("*"):
            try:
                if f.is_file(): f.unlink()
                elif f.is_dir(): shutil.rmtree(f)
            except OSError: pass
    return jsonify({"ok": True})


@app.route("/api/folder/open", methods=["POST"])
def api_open_folder():
    cfg = load_config()
    folder = Path(cfg.get("download_folder") or DEFAULT_FOLDER).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif sys.platform == "win32":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Boot ───────────────────────────────────────────────────────────────────
def open_browser_later():
    time.sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    log.info(f"🎵 Liked Songs Downloader → http://{HOST}:{PORT}")
    if not check_ffmpeg():
        log.warning("⚠️  ffmpeg no encontrado. La app abrirá igual y te avisará en la UI.")
    threading.Thread(target=open_browser_later, daemon=True).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)