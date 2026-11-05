from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import requests

from .settings import CATEGORIES

MEDIA_EXT = {
    *(ext for exts in CATEGORIES.values() for ext in exts),
    ".m3u8", ".mpd", ".m4s", ".aac", ".ac3", ".flv", ".f4v", ".mka", ".m3u",
    ".woff", ".woff2", ".ttf", ".otf", ".json", ".xml", ".sql", ".bak",
}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.base = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and values.get("href"):
            self.base = values["href"]
        for key in ("href", "src", "data"):
            value = values.get(key)
            if value:
                self.links.append((tag, value))
        if tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content") or ""
            if prop in ("og:video", "og:video:url", "og:audio", "og:audio:url", "twitter:player:stream"):
                self.links.append(("meta", content))


@dataclass
class LinkInfo:
    url: str
    kind: str
    size: int | None = None
    content_type: str = ""
    filename: str = ""
    error: str = ""

    @property
    def label(self) -> str:
        return self.filename or unquote(urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1]) or self.url


def _looks_like_file(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in MEDIA_EXT)


def extract_links(page_url: str, html: str, include_all: bool = False) -> list[LinkInfo]:
    parser = LinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    base = urljoin(page_url, parser.base) if parser.base else page_url
    found: dict[str, str] = {}
    for tag, raw in parser.links:
        raw = raw.strip()
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        absolute = urljoin(base, raw)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if not include_all and not _looks_like_file(absolute):
            # media tags are kept even when the extension is not in the table
            if tag not in ("video", "audio", "source", "meta", "embed", "img"):
                continue
        clean = absolute.split("#", 1)[0]
        found.setdefault(clean, tag)
    return [LinkInfo(url=url, kind=kind) for url, kind in found.items()]


def probe_link(url: str, referer: str = "", timeout: tuple[int, int] = (6, 12)) -> LinkInfo:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer
    info = LinkInfo(url=url, kind="file")
    try:
        with requests.Session() as session:
            response = session.head(url, headers=headers, allow_redirects=True, timeout=timeout)
            if response.status_code in (403, 405, 501) or not response.headers.get("Content-Length"):
                response.close()
                response = session.get(url, headers={**headers, "Range": "bytes=0-0"},
                                       allow_redirects=True, timeout=timeout, stream=True)
            info.url = response.url or url
            info.content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            length = response.headers.get("Content-Length")
            content_range = response.headers.get("Content-Range", "")
            match = re.search(r"/(\d+)\s*$", content_range)
            if match:
                info.size = int(match.group(1))
            elif length and length.isdigit():
                info.size = int(length)
            disposition = response.headers.get("Content-Disposition", "")
            match = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition, re.I)
            if match:
                info.filename = unquote(match.group(1).strip().strip('"'))
            else:
                match = re.search(r'filename\s*=\s*"([^"]+)"', disposition, re.I)
                if match:
                    info.filename = match.group(1)
            response.close()
    except Exception as exc:  # noqa: BLE001
        info.error = str(exc)
    return info


def grab(page_url: str, include_all: bool = False, max_links: int = 300) -> list[LinkInfo]:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = requests.get(page_url, headers=headers, timeout=(10, 25))
    response.raise_for_status()
    links = extract_links(page_url, response.text, include_all=include_all)[:max_links]
    if not links:
        return []

    def enrich(link: LinkInfo) -> LinkInfo:
        probed = probe_link(link.url, referer=page_url)
        probed.kind = link.kind
        if probed.size is None and not probed.error:
            probed.error = "Size unavailable"
        return probed

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(enrich, links))
