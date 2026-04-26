"""
Eye 3 tweet fetcher — Twikit wrapper with layered account-safety measures.

Safety layers (innermost → outermost):
  1. Cookie persistence   — session reused across all runs; login is rare.
  2. Stale-cookie recovery — if auth fails, cookies are deleted and re-login attempted once.
  3. Per-call jitter      — random 2–5 s sleep before every API call to look organic.
  4. Minimum call interval — enforces ≥ 45 s between any two search calls.
  5. Daily call cap        — hard limit of 48 search calls per calendar day.
  6. Backoff on rate-limit — if Twitter returns 429/TooManyRequests, pause 20 min before
                             any further calls (skips Eye 3 for that window rather than
                             hammering the API).
  7. Single call per run   — only one search_tweet call is ever issued per pipeline run.
  8. Graceful degradation  — any failure returns None; Eye 3 is skipped, not crashed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from twikit import Client
from twikit.errors import TooManyRequests, Unauthorized

# Make pipeline/ importable so we can read its config/settings.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PIPELINE_DIR = _REPO_ROOT / "pipeline"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from config import get_settings  # pipeline/config.py

log = logging.getLogger(__name__)

_COOKIES_PATH = Path(__file__).resolve().parent / "twikit_cookies.json"

# ── Tuneable safety constants ────────────────────────────────────────────────
_MIN_INTERVAL_SECONDS: float = 45.0
_MAX_DAILY_CALLS: int = 48
_JITTER_MIN: float = 2.0
_JITTER_MAX: float = 5.0
_BACKOFF_MINUTES: float = 20.0

# ── Runtime state ────────────────────────────────────────────────────────────
_client: Optional[Client] = None
_client_lock: Optional[asyncio.Lock] = None

_last_call_ts: float = 0.0
_backoff_until: Optional[datetime] = None

_daily_call_count: int = 0
_daily_reset_date: Optional[date] = None


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _reset_daily_counter_if_needed() -> None:
    global _daily_call_count, _daily_reset_date
    today = date.today()
    if _daily_reset_date != today:
        _daily_call_count = 0
        _daily_reset_date = today


async def _build_client() -> Client:
    settings = get_settings()
    client = Client("en-US")

    if _COOKIES_PATH.exists():
        try:
            client.load_cookies(str(_COOKIES_PATH))
            log.debug("twikit: loaded existing cookies from %s", _COOKIES_PATH)
            return client
        except Exception:
            log.warning("twikit: stale or corrupt cookies, re-authenticating")
            _COOKIES_PATH.unlink(missing_ok=True)

    log.info("twikit: performing first-time login for %s", settings.TWITTER_USERNAME)
    await client.login(
        auth_info_1=settings.TWITTER_USERNAME,
        auth_info_2=settings.TWITTER_EMAIL,
        password=settings.TWITTER_PASSWORD,
    )
    client.save_cookies(str(_COOKIES_PATH))
    log.info("twikit: login successful, cookies saved")
    return client


async def _get_client() -> Client:
    global _client
    if _client is not None:
        return _client
    async with _get_lock():
        if _client is not None:
            return _client
        _client = await _build_client()
        return _client


async def _checked_search(client: Client, query: str) -> Optional[str]:
    global _last_call_ts, _daily_call_count, _backoff_until, _client

    # Guard 1: active backoff window
    if _backoff_until is not None:
        now = datetime.now(timezone.utc)
        if now < _backoff_until:
            remaining = int((_backoff_until - now).total_seconds() / 60)
            log.warning("twikit: in backoff window, skipping (%d min remaining)", remaining)
            return None
        _backoff_until = None

    # Guard 2: daily cap
    _reset_daily_counter_if_needed()
    if _daily_call_count >= _MAX_DAILY_CALLS:
        log.warning("twikit: daily call cap (%d) reached, skipping", _MAX_DAILY_CALLS)
        return None

    # Guard 3: minimum interval between calls
    import time
    elapsed = time.monotonic() - _last_call_ts
    if elapsed < _MIN_INTERVAL_SECONDS:
        wait = _MIN_INTERVAL_SECONDS - elapsed
        log.debug("twikit: enforcing min interval, sleeping %.1f s", wait)
        await asyncio.sleep(wait)

    # Guard 4: organic jitter
    jitter = random.uniform(_JITTER_MIN, _JITTER_MAX)
    log.debug("twikit: pre-call jitter %.2f s", jitter)
    await asyncio.sleep(jitter)

    try:
        import time as _time
        _last_call_ts = _time.monotonic()
        _daily_call_count += 1
        log.info("twikit: search_tweet(query=%r) [day_count=%d]", query, _daily_call_count)

        results = await asyncio.wait_for(
            client.search_tweet(query, product="Latest", count=1),
            timeout=60.0,
        )

        if not results:
            log.info("twikit: no tweets found for query %r", query)
            return None

        text = results[0].text
        log.info("twikit: got tweet (%d chars)", len(text))
        return text

    except TooManyRequests:
        _backoff_until = datetime.now(timezone.utc) + timedelta(minutes=_BACKOFF_MINUTES)
        log.warning("twikit: 429 TooManyRequests — backing off until %s", _backoff_until.isoformat())
        return None

    except Unauthorized:
        log.warning("twikit: Unauthorized — clearing cookies for re-login on next run")
        _COOKIES_PATH.unlink(missing_ok=True)
        _client = None
        return None

    except asyncio.TimeoutError:
        log.warning("twikit: search_tweet timed out after 60 s")
        return None

    except Exception as exc:
        log.warning("twikit: unexpected error: %s", exc)
        return None


async def fetch_latest_tweet(
    event_type: str,
    disaster_date: str,
) -> Optional[str]:
    """
    Return the text of the most-recent tweet containing event_type near disaster_date.

    One Twikit call per invocation, with all safety guards applied.
    Returns None on any failure or guard trip.
    """
    try:
        d = date.fromisoformat(disaster_date)
        until_date = (d + timedelta(days=1)).isoformat()
        query = f"{event_type} since:{disaster_date} until:{until_date}"
    except ValueError:
        query = event_type

    try:
        client = await _get_client()
    except Exception as exc:
        log.warning("twikit: client init failed: %s", exc)
        return None

    return await _checked_search(client, query)
