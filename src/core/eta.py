"""core/eta.py — conservative ETA/completion-time estimation shared by the
Merge and Extract workers.

The naive "elapsed * (100-pct)/pct" extrapolation (linear from the AVERAGE
rate so far) runs optimistic whenever slow work is back-loaded relative to
fast work — stream copies land first, transcodes/archival passes later, so
the average rate looks good early and the ETA undershoots (confirmed
directly: real user reports of an inaccurate, over-optimistic countdown).
Two independent fixes, both applied here:

  1. `pct` is BYTE-based (produced / expected-total), not stage-count-based —
     bytes already weight transcode-heavy clips more honestly than counting
     every stage as equal work (see core.plan_report's est_bytes model,
     which is exactly where the expected-total figure should come from).
  2. The final ETA is the WORSE (longer) of the average-rate extrapolation
     and a "continues at the slowest recently-observed rate" extrapolation —
     deliberately conservative, so the estimate only ever surprises the user
     by finishing EARLY, never late.
"""

import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional


class ConservativeEta:
    """Feed it produced-byte counts as work progresses; it tracks a smoothed
    rate plus a rolling window of recent instantaneous rates, and estimates
    against the WORSE (slower) of the two — see module docstring.

    `clock` defaults to `time.time` — injectable so tests can drive it with
    a fake clock instead of real `time.sleep()` calls."""

    def __init__(self, window: int = 12, clock=time.time):
        self._clock = clock
        self._t0 = clock()
        self._last_t: Optional[float] = None
        self._last_bytes = 0
        self._rate_ewma = 0.0
        self._recent_rates: deque = deque(maxlen=window)

    def _update(self, produced_bytes: int) -> None:
        now = self._clock()
        if self._last_t is not None and now > self._last_t:
            dt = now - self._last_t
            dbytes = produced_bytes - self._last_bytes
            if dbytes >= 0 and dt > 0:
                inst = dbytes / dt
                self._rate_ewma = inst if not self._rate_ewma else 0.6 * self._rate_ewma + 0.4 * inst
                self._recent_rates.append(inst)
        self._last_t, self._last_bytes = now, produced_bytes

    def estimate(self, produced_bytes: int, expected_total_bytes: int) -> dict:
        """{pct, rate_bps, elapsed_secs, eta_secs, total_secs} — eta_secs/
        total_secs are None until there's enough signal to extrapolate from
        (guards the div-by-near-zero blowup at the very start)."""
        self._update(produced_bytes)
        elapsed = self._clock() - self._t0
        total = max(1, expected_total_bytes)
        frac = min(0.9999, max(0.0, produced_bytes / total))
        pct = frac * 100.0

        eta_avg = elapsed * (1 - frac) / frac if frac > 0.005 else None

        remaining = max(0, total - produced_bytes)
        slow_rate = min(self._recent_rates) if self._recent_rates else 0.0
        eta_slow = remaining / slow_rate if slow_rate > 0 else None

        candidates = [e for e in (eta_avg, eta_slow) if e is not None]
        eta = max(candidates) if candidates else None

        return {
            "pct": pct,
            "rate_bps": self._rate_ewma,
            "elapsed_secs": elapsed,
            "eta_secs": eta,
            "total_secs": (elapsed + eta) if eta is not None else None,
        }


def format_hms(secs: Optional[float]) -> str:
    """"14h22m34s" (hours omitted under 1h: "22m34s"; minutes omitted under
    1m: "34s"). None/negative -> "—"."""
    if secs is None or secs < 0:
        return "—"
    total = int(round(secs))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def format_completion(eta_secs: Optional[float], now: Optional[datetime] = None) -> str:
    """"21:23, Sunday 12 July 2026" — the wall-clock ETA's own completion
    format, or "—" when there's not yet enough signal to estimate."""
    if eta_secs is None or eta_secs < 0:
        return "—"
    when = (now or datetime.now()) + timedelta(seconds=eta_secs)
    return when.strftime("%H:%M, %A %d %B %Y")
