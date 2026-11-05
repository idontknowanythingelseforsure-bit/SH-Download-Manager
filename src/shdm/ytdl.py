"""yt-dlp integration for media pages (YouTube and similar sites).

Only used for pages that yt-dlp can extract; ordinary file links keep using the
segmented HTTP engine.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com",
    "vimeo.com", "dailymotion.com", "soundcloud.com", "bandcamp.com",
    "facebook.com", "fb.watch", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "twitch.tv", "reddit.com", "redd.it",
    "bilibili.com", "rumble.com", "odysee.com", "streamable.com",
    "v.redd.it", "pornhub.com", "xvideos.com", "xnxx.com",
    "ok.ru", "vk.com", "bitchute.com", "lbry.tv",
)

_AVAILABLE = None


class Cancelled(Exception):
    """Raised from the progress hook when the user pauses or removes the task."""


def available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import yt_dlp  # noqa: F401
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def can_handle(url: str) -> bool:
    if not url or not re.match(r"^https?://", url, re.I):
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    host = host[4:] if host.startswith("www.") else host
    return any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS)


def ffmpeg_dir() -> str | None:
    """Locate ffmpeg, preferring a copy shipped next to the application.

    The installer drops ffmpeg into ``<install dir>\\ffmpeg`` so MP3 output
    works on machines that never installed ffmpeg system-wide.
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return str(Path(exe).parent)

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe")
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(Path(bundle) / "ffmpeg" / "ffmpeg.exe")
    here = Path(__file__).resolve()
    candidates.extend([
        here.parents[2] / "ffmpeg" / "ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.parent)
    return None


def _base_opts(skip: bool = True) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "skip_download": skip,
        "socket_timeout": 20,
        "retries": 2,
        "cachedir": False,
        "extract_flat": False,
    }
    ff = ffmpeg_dir()
    if ff:
        opts["ffmpeg_location"] = ff
    return opts


def _first_entry(info: dict) -> dict:
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        if entries:
            return entries[0]
    return info


def _fmt_size(value) -> int | None:
    try:
        return int(value) if value else None
    except Exception:
        return None


def list_formats(url: str, limit: int = 12) -> list[dict]:
    """Return selectable video and audio formats for a media page."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_base_opts(skip=True)) as ydl:
        info = _first_entry(ydl.extract_info(url, download=False) or {})

    formats = info.get("formats") or []
    title = info.get("title") or ""

    # best audio stream for merging
    audio_candidates = [f for f in formats
                        if (f.get("acodec") not in (None, "none"))]
    audio_candidates.sort(key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0), reverse=True)
    best_audio = audio_candidates[0] if audio_candidates else None
    audio_size = _fmt_size(best_audio.get("filesize") or best_audio.get("filesize_approx")) \
        if best_audio else None

    # best video stream per height
    by_height: dict[int, dict] = {}
    for f in formats:
        if f.get("vcodec") in (None, "none"):
            continue
        height = f.get("height") or 0
        if not height:
            continue
        current = by_height.get(height)
        if current is None or (f.get("tbr") or 0) > (current.get("tbr") or 0):
            by_height[height] = f

    has_ffmpeg = ffmpeg_dir() is not None
    entries: list[dict] = []

    for height in sorted(by_height, reverse=True):
        stream = by_height[height]
        ext = (stream.get("ext") or "mp4").lower()
        size = _fmt_size(stream.get("filesize") or stream.get("filesize_approx"))
        merged = bool(stream.get("acodec") in (None, "none"))
        if size is not None and merged and audio_size:
            size += audio_size
        if merged and not has_ffmpeg:
            continue
        if height >= 2160:
            quality = "4K"
        elif height >= 1440:
            quality = "2K"
        elif height >= 1080:
            quality = "1080p HD"
        else:
            quality = "%dp" % height
        if merged:
            selector = ("bv*[height<=%d]+ba/b[height<=%d]" % (height, height))
            label = "MP4 file, quality %s (video + audio)" % quality
        else:
            selector = ("b[height<=%d][ext=%s]/b[height<=%d]" % (height, ext, height))
            label = "%s file, quality %s" % (ext.upper(), quality)
        entries.append({
            "id": "v%d" % height,
            "kind": "video",
            "label": label,
            "ext": "mp4" if merged else ext,
            "height": height,
            "selector": selector,
            "size": size,
            "title": title,
        })

    # audio-only choices: MP3 in two bitrates, converted with ffmpeg
    duration = info.get("duration")
    if has_ffmpeg:
        for quality in (320, 128):
            size = None
            try:
                if duration:
                    size = int(float(duration) * quality * 1000 / 8)
            except Exception:
                size = None
            entries.append({
                "id": "a_mp3_%d" % quality,
                "kind": "audio",
                "label": "Audio only, MP3 (%d kbps)" % quality,
                "ext": "mp3",
                "abr": quality,
                "selector": "ba/b|mp3:%d" % quality,
                "size": size,
                "title": title,
            })

    if not entries:
        entries.append({
            "id": "best",
            "kind": "video",
            "label": "Best available quality",
            "ext": "mp4",
            "selector": "bv*+ba/b" if has_ffmpeg else "b",
            "size": None,
            "title": title,
        })

    return entries[:limit]


def download(url: str, selector: str, save_dir: Path, stem: str,
             progress=None, stop=None) -> tuple[Path, dict]:
    """Download a media page. Returns (final_path, info)."""
    import yt_dlp

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", stem or "download").strip(" .") or "download"

    opts = _base_opts(skip=False)
    selector = selector or "bv*+ba/b"
    audio_quality = None
    if "|mp3:" in selector:
        selector, _, quality = selector.partition("|mp3:")
        try:
            audio_quality = int(quality.strip())
        except ValueError:
            audio_quality = 192
        selector = selector or "ba/b"
    opts.update({
        "format": selector,
        "outtmpl": str(save_dir / (stem + ".%(ext)s")),
        "windowsfilenames": True,
        "overwrites": False,
        "continuedl": True,
        "nopart": False,
    })
    if audio_quality is not None:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(audio_quality),
        }]
    else:
        opts["merge_output_format"] = "mp4"
    if ffmpeg_dir():
        opts["ffmpeg_location"] = ffmpeg_dir()

    def hook(data):
        if stop is not None and stop():
            raise Cancelled()
        if progress is not None:
            try:
                progress(data)
            except Cancelled:
                raise
            except Exception:
                pass

    opts["progress_hooks"] = [hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
        path = None
        requested = info.get("requested_downloads") or []
        if requested:
            path = requested[0].get("filepath")
        if not path:
            path = ydl.prepare_filename(info)
        final = Path(path)
        if audio_quality is not None:
            converted = final.with_suffix(".mp3")
            if converted.exists():
                final = converted
        if not final.exists():
            matches = sorted(save_dir.glob(stem + ".*"))
            matches = [m for m in matches if not m.name.endswith(".part")]
            if audio_quality is not None:
                mp3s = [m for m in matches if m.suffix.lower() == ".mp3"]
                if mp3s:
                    matches = mp3s
            if matches:
                final = matches[0]
    if not final.exists():
        raise RuntimeError("yt-dlp finished without producing a file.")
    return final, info
