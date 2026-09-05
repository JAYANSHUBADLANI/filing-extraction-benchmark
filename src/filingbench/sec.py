"""Rate limited, disk cached HTTP client for SEC EDGAR.

The SEC asks for a declared contact in the User-Agent and no more than ten
requests a second. A missing User-Agent returns 403, verified against
data.sec.gov on 2026-09-04. This client stays under the rate limit and never
refetches a URL it already has on disk, so a rerun costs no network traffic.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import CACHE_DIR, sec_user_agent

log = logging.getLogger(__name__)

MAX_REQUESTS_PER_SECOND = 8.0
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
MAX_RETRIES = 4


class RateLimiter:
    """Simple wall clock spacing between requests, safe across threads."""

    def __init__(self, per_second: float = MAX_REQUESTS_PER_SECOND) -> None:
        self._min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._min_interval - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


_LIMITER = RateLimiter()


def cache_path_for(url: str) -> Path:
    """A stable, readable cache path derived from the URL.

    The host and path shape the directory so a human can find a cached file,
    and a short hash of the full URL keeps distinct query strings apart.
    """
    parsed = urlparse(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    parts = [p for p in parsed.path.split("/") if p]
    tail = parts[-1] if parts else "index"
    stem = Path(tail).stem[:60] or "index"
    suffix = Path(tail).suffix or ".bin"
    return CACHE_DIR / parsed.netloc / f"{stem}.{digest}{suffix}"


class SecClient:
    """Fetches SEC URLs, caching every response body to disk."""

    def __init__(self, user_agent: str | None = None, cache_dir: Path | None = None) -> None:
        self.user_agent = user_agent or sec_user_agent()
        self.cache_dir = cache_dir or CACHE_DIR
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.network_requests = 0
        self.cache_hits = 0
        self.bytes_downloaded = 0

    def get_bytes(self, url: str, force: bool = False) -> bytes:
        path = cache_path_for(url)
        if path.exists() and not force:
            self.cache_hits += 1
            return path.read_bytes()

        body = self._download(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body

    def get_text(self, url: str, force: bool = False) -> str:
        raw = self.get_bytes(url, force=force)
        for encoding in ("utf-8", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def get_json(self, url: str, force: bool = False):
        import json

        return json.loads(self.get_bytes(url, force=force))

    def _download(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            _LIMITER.wait()
            try:
                response = self.session.get(url, timeout=120)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2.0 * (attempt + 1))
                continue

            self.network_requests += 1
            if response.status_code == 200:
                self.bytes_downloaded += len(response.content)
                return response.content
            if response.status_code == 404:
                raise FileNotFoundError(f"404 for {url}")
            if response.status_code in RETRY_STATUS:
                last_error = RuntimeError(f"HTTP {response.status_code} for {url}")
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {response.status_code} for {url}")

        raise RuntimeError(f"giving up on {url}: {last_error}")


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def companyfacts_url(cik: int) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


def document_url(cik: int, accession: str, primary_document: str) -> str:
    nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{primary_document}"
