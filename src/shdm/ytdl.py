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


def platform_muxer():
    """A callable (video, audio, output) that merges streams, or None.

    Windows uses the bundled ffmpeg through yt-dlp itself, so this is only
    needed where yt-dlp has no ffmpeg (the Android build uses MediaMuxer).
    """
    if ffmpeg_dir():
        return None
    try:
        from .mobile import device

        if device.can_mux():
            return device.mux_mp4
    except Exception:
        pass
    return None


def video_selector() -> str:
    """Format selector for the VIDEO option in the Add dialog."""
    if ffmpeg_dir():
        return "bv*+ba/b"
    if platform_muxer() is not None:
        # Two streams joined by MediaMuxer; H.264 + AAC is the safe pair.  The
        # plain-HTTP (progressive) stream is preferred because MediaExtractor
        # reads it on every supported Android version; fragmented m3u8 mp4 is
        # the fallback for videos that only publish segmented streams.
        return ("bv*[ext=mp4][vcodec^=avc1][protocol^=http]/"
                "bv*[ext=mp4][vcodec^=avc1]/bv*[ext=mp4]"
                "+ba[ext=m4a]")
    return "b"


def audio_selector() -> str:
    """Format selector for the AUDIO option in the Add dialog."""
    if ffmpeg_dir():
        return "ba/b|mp3:192"
    return "ba[ext=m4a]/ba/b"


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
    can_merge = has_ffmpeg or platform_muxer() is not None
    entries: list[dict] = []

    for height in sorted(by_height, reverse=True):
        stream = by_height[height]
        ext = (stream.get("ext") or "mp4").lower()
        size = _fmt_size(stream.get("filesize") or stream.get("filesize_approx"))
        merged = bool(stream.get("acodec") in (None, "none"))
        if size is not None and merged and audio_size:
            size += audio_size
        if merged and not can_merge:
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
            if has_ffmpeg:
                selector = ("bv*[height<=%d]+ba/b[height<=%d]" % (height, height))
            else:
                selector = ("bv*[height<=%d][ext=mp4][vcodec^=avc1]"
                            "[protocol^=http]/bv*[height<=%d][ext=mp4][vcodec^=avc1]"
                            "+ba[ext=m4a]" % (height, height))
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
            "selector": "bv*+ba/b" if has_ffmpeg else video_selector(),
            "size": None,
            "title": title,
        })

    return entries[:limit]


def _grab(url: str, selector: str, outtmpl: Path, base: dict, hook) -> tuple[Path, dict]:
    """One yt-dlp download with its own format selector and output name."""
    import yt_dlp

    opts = dict(base)
    opts.pop("postprocessors", None)
    opts["format"] = selector
    opts["outtmpl"] = str(outtmpl)
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
    if not final.exists():
        raise RuntimeError("yt-dlp finished without producing a file.")
    return final, info


def _download_and_merge(url: str, selector: str, save_dir: Path, stem: str,
                        opts: dict, progress, stop, muxer) -> tuple[Path, dict]:
    """Fetch the video and audio streams separately, then mux them.

    Used where yt-dlp has no ffmpeg (the Android build): the platform muxer
    copies the H.264 video track and the AAC audio track without re-encoding.
    """
    video_sel, _, audio_sel = selector.partition("+")
    video_sel = video_sel.strip() or "bv*[ext=mp4]"
    audio_sel = audio_sel.strip() or "ba[ext=m4a]"

    state = {"done": 0, "total": 0}

    def tracked_hook(_name):
        def hook(data):
            if stop is not None and stop():
                raise Cancelled()
            status = data.get("status")
            if status == "downloading" and progress is not None:
                total = int(data.get("total_bytes")
                            or data.get("total_bytes_estimate") or 0)
                if total:
                    state["total"] = max(state["total"], state["done"] + total)
                payload = dict(data)
                payload["downloaded_bytes"] = (
                    state["done"] + int(data.get("downloaded_bytes") or 0))
                if state["total"]:
                    payload["total_bytes"] = state["total"]
                    payload["total_bytes_estimate"] = state["total"]
                try:
                    progress(payload)
                except Cancelled:
                    raise
                except Exception:
                    pass
            elif status == "finished":
                state["done"] += int(data.get("total_bytes")
                                     or data.get("downloaded_bytes") or 0)
        return hook

    pair_error = None
    video_path = audio_path = None
    video_info: dict = {}
    try:
        video_path, video_info = _grab(
            url, video_sel, save_dir / (stem + ".video.%(ext)s"),
            opts, tracked_hook("video"))
        audio_path, _audio_info = _grab(
            url, audio_sel, save_dir / (stem + ".audio.%(ext)s"),
            opts, tracked_hook("audio"))
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - fall back to a single-file format
        pair_error = exc
        for pattern in (".part", ".ytdl", ".vtt"):
            for leftover in save_dir.glob(stem + "*" + pattern):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        for side in (".video.", ".audio."):
            for leftover in save_dir.glob(stem + side + "*"):
                if leftover.name.endswith(".part"):
                    continue
                try:
                    leftover.unlink()
                except OSError:
                    pass

    if video_path is None or audio_path is None:
        # A few videos still publish one muxed file; use it when it exists.
        try:
            single, info = _grab(url, "b[ext=mp4]/b", save_dir / (stem + ".%(ext)s"),
                                 opts, tracked_hook("file"))
            return single, info
        except Cancelled:
            raise
        except Exception:
            detail = str(pair_error or "no matching streams")
            raise RuntimeError(
                "This video only offers separate video and audio streams and "
                f"they could not be merged ({detail}). Try AUDIO instead."
            ) from pair_error

    out = save_dir / (stem + ".mp4")
    try:
        muxer(video_path, audio_path, out)
    except Exception as exc:  # noqa: BLE001 - surface a readable reason
        # Keep the two streams: a retry only has to mux them again.
        try:
            if Path(out).exists():
                Path(out).unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Video and audio downloaded, but merging them failed ({exc})."
        ) from exc

    for part in (video_path, audio_path):
        try:
            Path(part).unlink()
        except OSError:
            pass

    info = dict(video_info or {})
    return out, info


def download(url: str, selector: str, save_dir: Path, stem: str,
             progress=None, stop=None, muxer=None) -> tuple[Path, dict]:
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

    if ffmpeg_dir() is None and "+" in selector and audio_quality is None:
        if muxer is None:
            muxer = platform_muxer()
        if muxer is None:
            raise RuntimeError(
                "This video only offers separate video and audio streams, and "
                "this build has no way to merge them. Try AUDIO instead."
            )
        return _download_and_merge(url, selector, save_dir, stem, opts,
                                   progress, stop, muxer)

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
