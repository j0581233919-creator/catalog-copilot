"""In-memory thread-safe rate limiter and request throttler for CatalogCopilot.

Provides pure standard-library sliding-window throttling to safeguard:
- Chatwoot webhook endpoints from message cascades and rapid-fire spam.
- Batch CLI processing pipelines from resource exhaustion.
- Outbound customer channels from unauthorized burst messaging.
"""

from __future__ import annotations
import time
import threading
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any, Callable


class RateLimiter:
    """Sliding-window in-memory rate limiter with per-key tracking and burst control."""

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: float = 60.0,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than 0")

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._time_fn = time_fn or time.time
        self._lock = threading.Lock()
        # Mapping from key -> list of float timestamps
        self._timestamps: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Returns True if a request for the given key is currently within rate limits."""
        allowed, _ = self.check_and_consume(key, consume=False)
        return allowed

    def check_and_consume(
        self,
        key: str,
        cost: int = 1,
        consume: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Evaluates whether the key is permitted to proceed, and atomically consumes tokens if allowed."""
        now = self._time_fn()
        window_start = now - self.window_seconds

        with self._lock:
            ts_list = self._timestamps[key]
            # Prune timestamps older than current sliding window
            ts_list = [t for t in ts_list if t > window_start]
            self._timestamps[key] = ts_list

            current_count = len(ts_list)
            remaining = max(0, self.max_requests - current_count)

            if current_count + cost <= self.max_requests:
                if consume:
                    for _ in range(cost):
                        ts_list.append(now)
                    remaining = max(0, self.max_requests - len(ts_list))

                return True, {
                    "allowed": True,
                    "remaining": remaining,
                    "limit": self.max_requests,
                    "reset_in_seconds": round(self.window_seconds, 2),
                    "retry_after_seconds": 0.0,
                }
            else:
                # Calculate when the oldest request in the window expires
                oldest_ts = ts_list[0] if ts_list else now
                retry_after = max(0.0, round((oldest_ts + self.window_seconds) - now, 2))

                return False, {
                    "allowed": False,
                    "remaining": 0,
                    "limit": self.max_requests,
                    "reset_in_seconds": round(self.window_seconds, 2),
                    "retry_after_seconds": retry_after,
                }

    def reset(self, key: Optional[str] = None) -> None:
        """Resets rate limit counter for a specific key, or for all keys if key is None."""
        with self._lock:
            if key is None:
                self._timestamps.clear()
            else:
                self._timestamps.pop(key, None)

    def cleanup_expired(self) -> int:
        """Removes all keys with no active timestamps in the current window."""
        now = self._time_fn()
        window_start = now - self.window_seconds
        cleaned = 0

        with self._lock:
            keys_to_delete = []
            for k, ts_list in self._timestamps.items():
                active = [t for t in ts_list if t > window_start]
                if not active:
                    keys_to_delete.append(k)
                else:
                    self._timestamps[k] = active
            for k in keys_to_delete:
                del self._timestamps[k]
                cleaned += 1

        return cleaned
