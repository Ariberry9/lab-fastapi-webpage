"""Per-user avatars: download robohash kitten PNGs once, then serve them
from `static/avatars/<username>.png`.

The website never hits robohash at request time:

* `download_all_avatars(...)` is called from `db_create.py` to populate every
  user with their unique kitten image.
* `ensure_avatar(...)` is called from `app.py` when a brand-new user signs up.
* Any download that ultimately fails falls back to a generic `_default.png`
  which we make sure is in place first.

All HTTP is done with the stdlib (no extra dependencies). Downloads are run
concurrently with a small thread pool so 200 users finish in a few seconds.
"""
from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

# robohash set=set4 is the kitten set; size 200x200 looks good on every
# place the template uses (small, medium, large) thanks to CSS scaling.
ROBOHASH_URL = "https://robohash.org/{name}?set=set4&size=200x200"
DEFAULT_AVATAR_NAME = "_default.png"
_USER_AGENT = "FastAPI-Lab/1.0 (+local)"
_MIN_VALID_BYTES = 200  # smaller responses are almost certainly error pages


def _http_get_png(url: str, timeout: float) -> bytes | None:
    """Fetch a URL once and return its bytes, or None on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            return data if len(data) >= _MIN_VALID_BYTES else None
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError, OSError):
        return None


def _download_with_retries(
    url: str,
    dest: Path,
    *,
    timeout: float = 15.0,
    retries: int = 3,
) -> bool:
    """Download `url` into `dest`, retrying on transient errors."""
    for attempt in range(retries):
        data = _http_get_png(url, timeout=timeout)
        if data is not None:
            dest.write_bytes(data)
            return True
        if attempt < retries - 1:
            time.sleep(0.5 + attempt)  # gentle backoff
    return False


def _username_url(username: str) -> str:
    return ROBOHASH_URL.format(name=urllib.parse.quote(username, safe=""))


def download_avatar(
    username: str,
    target_dir: Path,
    *,
    timeout: float = 15.0,
    retries: int = 3,
) -> bool:
    """Try to fetch `username`'s kitten avatar into `target_dir`.

    Returns True if the resulting PNG is on disk.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / f"{username}.png"
    return _download_with_retries(
        _username_url(username), dest, timeout=timeout, retries=retries,
    )


def ensure_default_avatar(target_dir: Path) -> Path:
    """Make sure `target_dir / _default.png` exists, even if robohash is down.

    Tries to grab a neutral kitten first; if every attempt fails we leave the
    path empty and callers will simply skip the fallback copy.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / DEFAULT_AVATAR_NAME
    if dest.exists() and dest.stat().st_size >= _MIN_VALID_BYTES:
        return dest
    _download_with_retries(
        _username_url("default-kitten"),
        dest,
        timeout=20.0,
        retries=5,
    )
    return dest


def _copy_default_into(
    username: str, target_dir: Path, default: Path,
) -> bool:
    if not default.exists() or default.stat().st_size < _MIN_VALID_BYTES:
        return False
    try:
        shutil.copyfile(default, target_dir / f"{username}.png")
        return True
    except OSError:
        return False


def ensure_avatar(username: str, target_dir: Path) -> Path:
    """Make sure `target_dir/<username>.png` exists.

    Order of preference:
      1. existing file (cached from a previous run)
      2. fresh download from robohash
      3. copy of the local `_default.png`

    Always returns the destination path, even if the file ended up missing.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / f"{username}.png"

    if dest.exists() and dest.stat().st_size >= _MIN_VALID_BYTES:
        return dest

    # Short timeout when called from a request handler so we never block
    # the user-facing /create_user response for long.
    if download_avatar(username, target_dir, timeout=8.0, retries=2):
        return dest

    default = target_dir / DEFAULT_AVATAR_NAME
    if not default.exists():
        ensure_default_avatar(target_dir)
    _copy_default_into(username, target_dir, default)
    return dest


def download_all_avatars(
    usernames: Iterable[str],
    target_dir: Path,
    *,
    max_workers: int = 10,
) -> dict:
    """Download avatars for many users concurrently.

    Returns a summary dict like ``{"ok": 195, "fallback": 5, "missing": 0}``.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    ensure_default_avatar(target_dir)

    usernames = list(usernames)
    succeeded: list[str] = []
    failed: list[str] = []

    if usernames:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(download_avatar, name, target_dir): name
                for name in usernames
            }
            for fut in as_completed(futures):
                name = futures[fut]
                if fut.result():
                    succeeded.append(name)
                else:
                    failed.append(name)

    default = target_dir / DEFAULT_AVATAR_NAME
    fallback = 0
    missing = 0
    for name in failed:
        if _copy_default_into(name, target_dir, default):
            fallback += 1
        else:
            missing += 1

    return {
        "ok": len(succeeded),
        "fallback": fallback,
        "missing": missing,
    }
