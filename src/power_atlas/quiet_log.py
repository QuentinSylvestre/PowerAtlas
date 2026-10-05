"""Log one kind of failure at most once a minute, naming only the exception class.

A leaf module: it imports nothing from this package, so the lock-owner lookup, the
notifier and the state-database reader can all use it. A line carries the kind and
the exception class, never a path, a session id or a message, because these logs
are read in transcripts.
261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 1 (D20)
"""
from __future__ import annotations

import logging
import threading
import time

INTERVAL_S = 60.0
# Tests replace the clock to step past the interval without sleeping.
_clock = time.monotonic
_lock = threading.Lock()
_last: dict[str, float] = {}


def warn(logger: logging.Logger, kind: str, exc: BaseException | None = None) -> bool:
    """Log `kind` (and the class of `exc`) unless the same kind was logged less than
    INTERVAL_S ago. Returns whether a line was written."""
    now = _clock()
    with _lock:
        last = _last.get(kind)
        if last is not None and now - last < INTERVAL_S:
            return False
        _last[kind] = now
    suffix = f" ({type(exc).__name__})" if exc is not None else ""
    logger.warning("%s%s", kind, suffix)
    return True


def reset() -> None:
    """Forget every kind, for the tests' autouse fixtures."""
    with _lock:
        _last.clear()
