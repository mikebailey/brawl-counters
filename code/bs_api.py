"""Minimal Brawl Stars API client: rate limiting, retries, clear failures.

Stdlib only, so the project runs in a bare interpreter with no install step.

The API is IP-locked at the key level, which produces the single most confusing
failure in this project: a 403 whose message says nothing about IP addresses.
`_raise_helpful` turns that into an actionable message.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.brawlstars.com/v1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_key() -> str:
    """Read BRAWL_API_KEY from the project .env (hand-parsed, no dependency)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise SystemExit(f"No .env at {env_path}. See .env.example.")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "BRAWL_API_KEY":
            key = value.strip().strip("'\"")
            if not key:
                raise SystemExit("BRAWL_API_KEY is empty in .env.")
            return key
    raise SystemExit("BRAWL_API_KEY not found in .env.")


class RateLimiter:
    """Token bucket shared across threads.

    Supercell does not publish a rate limit, so we self-impose one well under
    where throttling has been observed. Being polite here costs a few minutes
    on a long run and avoids getting the key throttled.
    """

    def __init__(self, per_second: float):
        self._min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._min_interval


class NotFound(Exception):
    """Resource does not exist (404). Routine -- players get renamed/deleted."""


class BrawlStarsAPI:
    def __init__(self, key: str | None = None, per_second: float = 10.0,
                 max_retries: int = 4):
        self.key = key or load_key()
        self.limiter = RateLimiter(per_second)
        self.max_retries = max_retries
        # Observability: callers print these so a long run is not a black box.
        self.n_requests = 0
        self.n_retries = 0
        self._counter_lock = threading.Lock()

    # -- internals ---------------------------------------------------------

    def _raise_helpful(self, err: urllib.error.HTTPError, path: str):
        if err.code == 403:
            raise SystemExit(
                f"\nHTTP 403 on {path}.\n"
                "This nearly always means the key's IP whitelist no longer matches\n"
                "this machine (home IPs rotate). Check your current IP and update the\n"
                "key's allowed addresses at https://developer.brawlstars.com --\n"
                "the token itself does not change.\n"
                "Diagnose with: python code/check_key.py\n"
            )
        raise err

    def get(self, path: str) -> dict:
        """GET a path, retrying transient failures with exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self.limiter.acquire()
            req = urllib.request.Request(
                API_BASE + path,
                headers={"Authorization": f"Bearer {self.key}",
                         "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    with self._counter_lock:
                        self.n_requests += 1
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise NotFound(path)
                if e.code in (429, 500, 502, 503, 504):
                    # Transient: back off and try again.
                    last_exc = e
                    with self._counter_lock:
                        self.n_retries += 1
                    time.sleep(2 ** attempt)
                    continue
                self._raise_helpful(e, path)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_exc = e
                with self._counter_lock:
                    self.n_retries += 1
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{path} failed after {self.max_retries} attempts: {last_exc}")

    # -- endpoints ---------------------------------------------------------

    def brawlers(self) -> list[dict]:
        return self.get("/brawlers")["items"]

    def rankings(self, country: str = "global", limit: int = 200) -> list[dict]:
        """Top players for a country code ('global' or ISO 3166-1 alpha-2)."""
        return self.get(f"/rankings/{country}/players?limit={limit}")["items"]

    def battlelog(self, tag: str) -> list[dict]:
        """The player's ~25 most recent battles.

        This is a short rolling window that expires, which is why collection is
        designed to run repeatedly and accumulate rather than once.
        """
        return self.get(f"/players/{urllib.parse.quote(tag)}/battlelog")["items"]


# Verified working 2026-08-16: every one of these returned a populated ranking.
COUNTRY_CODES = (
    "ad ae af ag al am ao ar at au az ba bd be bg bh bo br by ca ch cl cn co cr "
    "cy cz de dk do dz ec ee eg es fi fr gb ge gr gt hk hn hr hu id ie il in iq "
    "ir is it jo jp ke kr kw kz lb lt lu lv ma md me mk mn mt mx my ng ni nl no "
    "nz om pa pe ph pk pl pt py qa ro rs ru sa se sg si sk sv th tn tr tw ua us "
    "uy uz ve vn za"
).split()
