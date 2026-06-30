"""core/updates.py — lightweight 'check for updates' against GitHub Releases.

Makes one HTTPS request to the Releases API, compares the latest tag to the
running version, and reports whether a newer build exists. It never downloads or
installs anything — the UI just offers a link. Set UPDATE_REPO to enable; if it's
blank the check is skipped silently.
"""

import re
from typing import Optional, Tuple

# e.g. "jdm525/lunavault-fusebox" — leave blank to disable the check.
UPDATE_REPO = ""
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"


def _parse_version(s: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` is a strictly higher version than `current`."""
    return _parse_version(latest) > _parse_version(current)


def check_for_update(current_version: str, timeout: float = 5.0) -> Optional[dict]:
    """Return {'latest','url'} if a newer release exists, else None.

    Returns None on any error or if UPDATE_REPO is unset — the check is strictly
    best-effort and never blocks or raises into the UI.
    """
    if not UPDATE_REPO:
        return None
    try:
        import requests
        api = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
        r = requests.get(api, timeout=timeout,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        tag = (r.json() or {}).get("tag_name", "")
        if tag and is_newer(tag, current_version):
            return {"latest": tag,
                    "url": RELEASES_PAGE.format(repo=UPDATE_REPO)}
    except Exception:
        return None
    return None
