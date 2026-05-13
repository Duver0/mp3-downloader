#!/usr/bin/env python3
"""
Agrega metadatos (artista, título, carátula) a los MP3 descargados.
Lee data/songs.json para obtener la metadata de cada canción y usa
la caché de YouTube para descargar las carátulas.

Uso:
  python tag_songs.py                              # carpeta por defecto (config.json)
  python tag_songs.py --folder /ruta/a/mis/mp3     # carpeta personalizada
  python tag_songs.py --no-cover                   # solo tags, sin carátulas
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error as ID3Error
from mutagen.mp3 import MP3

APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
SONGS_PATH = DATA_DIR / "songs.json"
CACHE_PATH = DATA_DIR / "youtube_cache.json"


def sanitize(name: str, max_len: int = 180) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "untitled"
    encoded = name.encode("utf-8")
    if len(encoded) > max_len:
        while len(name) > 1 and len(name.encode("utf-8")) > max_len:
            name = name[:-1]
    return name


def load_songs() -> list[dict]:
    if not SONGS_PATH.exists():
        return []
    try:
        return json.loads(SONGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def download_thumbnail(video_id: str, dest: Path) -> bool:
    for res in ("maxresdefault", "hqdefault", "mqdefault", "sddefault"):
        url = f"https://img.youtube.com/vi/{video_id}/{res}.jpg"
        try:
            r = requests.get(url, timeout=5)
            if r.ok:
                dest.write_bytes(r.content)
                return True
        except Exception:
            continue
    return False


def get_cover_path(download_dir: Path, song_name: str) -> Path | None:
    safe = sanitize(song_name)
    for ext in (".jpg", ".png"):
        p = download_dir / f"{safe}{ext}"
        if p.exists():
            return p
    return None


def tag_file(mp3_path: Path, title: str, artist: str, cover_path: Path | None) -> bool:
    try:
        audio = MP3(mp3_path)
    except Exception as e:
        print(f"    ⚠ no se pudo leer: {e}")
        return False

    try:
        audio.add_tags()
    except ID3Error:
        pass

    audio.tags.add(TIT2(encoding=3, text=title))
    if artist:
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=artist))

    if cover_path and cover_path.exists():
        img_data = cover_path.read_bytes()
        mime = "image/jpeg" if cover_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        audio.tags.add(APIC(
            encoding=3, mime=mime, type=3, desc="Cover", data=img_data,
        ))

    try:
        audio.save()
        return True
    except Exception as e:
        print(f"    ⚠ no se pudo guardar: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Agregar metadatos (artista, título, carátula) a MP3 descargados",
    )
    parser.add_argument("--folder", "-f", default=None,
                        help="Carpeta con los MP3 (usa config.json si no se especifica)")
    parser.add_argument("--no-cover", "-nc", action="store_true",
                        help="No descargar ni incrustar carátulas")
    args = parser.parse_args()

    # ── carpeta destino ──
    if args.folder:
        download_dir = Path(args.folder).expanduser()
    elif CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            download_dir = Path(cfg.get("download_folder", "") or "").expanduser()
        except Exception:
            download_dir = Path.home() / "Downloads" / "Liked Songs"
    else:
        download_dir = Path.home() / "Downloads" / "Liked Songs"

    if not download_dir.exists():
        print(f"❌ La carpeta '{download_dir}' no existe")
        sys.exit(1)

    songs = load_songs()
    cache = load_cache()

    if not songs:
        print("❌ No hay canciones en data/songs.json")
        sys.exit(1)

    # build lookup: sanitized name → song metadata
    song_lookup: dict[str, dict] = {}
    for s in songs:
        key = sanitize(s.get("name", ""))
        if key and key != "untitled":
            song_lookup[key] = s

    mp3_files = sorted(download_dir.glob("*.mp3"))
    if not mp3_files:
        print(f"❌ No hay archivos .mp3 en '{download_dir}'")
        sys.exit(1)

    print(f"📁 {download_dir}")
    print(f"🎵 {len(mp3_files)} MP3 encontrados · {len(songs)} canciones en catálogo\n")

    tagged = 0
    skipped = 0
    errors = 0

    for mp3 in mp3_files:
        stem = mp3.stem  # filename without extension
        song = song_lookup.get(stem)

        if not song:
            print(f"  ⚠ sin metadata: {mp3.name}")
            skipped += 1
            continue

        name = song.get("name", "")
        artists = song.get("artists", [])
        artist = artists[0] if artists else ""
        track_id = song.get("id", "")
        label = f"{artist} - {name}".strip(" -") or name

        # carátula
        cover = None
        if not args.no_cover:
            cover = get_cover_path(download_dir, name)
            if cover is None:
                vid = None
                if track_id.startswith("txt:") and artist and name:
                    cached = cache.get(f"{artist}|{name}")
                    if cached:
                        vid = cached.get("id")
                if vid:
                    cover_path = download_dir / f"{sanitize(name)}.jpg"
                    if download_thumbnail(vid, cover_path):
                        print(f"  🖼️  carátula descargada: {name}")
                        cover = cover_path
                    else:
                        cover = None

        if tag_file(mp3, name, artist, cover):
            print(f"  ✓ {label}")
            tagged += 1
        else:
            print(f"  ✗ {label}")
            errors += 1

    print(f"\n✅ {tagged} etiquetados")
    if skipped:
        print(f"⏭️  {skipped} sin metadata (nombres no coinciden)")
    if errors:
        print(f"❌ {errors} errores")


if __name__ == "__main__":
    main()
