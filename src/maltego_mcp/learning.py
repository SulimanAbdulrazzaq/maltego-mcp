"""Outcome-based learning store for the Next Best Action engine.

Aggregates per-transform outcomes *across investigations* (not just the current
graph) so the NBA engine can favour transforms that have historically produced
value. Backed by a small JSON file.

**Opt-in by design.** Learning is disabled unless the user enables it, so default
behaviour — and therefore existing deterministic tests and evaluations — is
unchanged. Enable it by either:

* setting ``MALTEGO_MCP_LEARNING=1`` (uses the default path
  ``~/.maltego_mcp/learning.json``), or
* setting ``MALTEGO_MCP_LEARNING_PATH=/some/file.json`` (implies enabled).

When disabled, :func:`record` is a no-op and :func:`prior` returns a neutral
zero prior, so the recommendation engine behaves exactly as it does today.

The store records, per transform name: number of runs, number of successes
(ran and produced ≥1 new entity), and total new entities produced. From these it
derives a deterministic success rate and average yield.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional

ENABLE_ENV = "MALTEGO_MCP_LEARNING"
PATH_ENV = "MALTEGO_MCP_LEARNING_PATH"
DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".maltego_mcp", "learning.json")

_TRUTHY = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """True if learning is enabled (explicit flag or an explicit path)."""

    if os.environ.get(PATH_ENV):
        return True
    return (os.environ.get(ENABLE_ENV, "").strip().lower()) in _TRUTHY


def store_path() -> str:
    return os.environ.get(PATH_ENV) or DEFAULT_PATH


class LearningStore:
    """Thread-safe, JSON-backed aggregate of transform outcomes."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, float]] = {}
        self._loaded_from: Optional[str] = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        path = store_path()
        if self._loaded_from == path:
            return
        self._data = {}
        self._loaded_from = path
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self._data = {
                        k: {
                            "runs": float(v.get("runs", 0)),
                            "successes": float(v.get("successes", 0)),
                            "total_new": float(v.get("total_new", 0)),
                        }
                        for k, v in raw.items()
                        if isinstance(v, dict)
                    }
            except (ValueError, OSError):
                self._data = {}

    def record(self, transform: str, status: str, new_count: int) -> None:
        """Record one transform outcome (no-op when learning is disabled)."""

        if not is_enabled():
            return
        with self._lock:
            self._ensure_loaded()
            row = self._data.setdefault(
                transform, {"runs": 0.0, "successes": 0.0, "total_new": 0.0}
            )
            row["runs"] += 1
            if status == "success" and new_count > 0:
                row["successes"] += 1
                row["total_new"] += new_count

    def stats(self, transform: str) -> Dict[str, float]:
        """Return aggregate stats for a transform (zeros if unseen/disabled)."""

        if not is_enabled():
            return {"runs": 0.0, "successes": 0.0, "total_new": 0.0,
                    "success_rate": 0.0, "avg_yield": 0.0}
        with self._lock:
            self._ensure_loaded()
            row = self._data.get(transform)
        if not row or row["runs"] == 0:
            return {"runs": 0.0, "successes": 0.0, "total_new": 0.0,
                    "success_rate": 0.0, "avg_yield": 0.0}
        success_rate = row["successes"] / row["runs"]
        avg_yield = (row["total_new"] / row["successes"]) if row["successes"] else 0.0
        return {
            "runs": row["runs"],
            "successes": row["successes"],
            "total_new": row["total_new"],
            "success_rate": round(success_rate, 3),
            "avg_yield": round(avg_yield, 3),
        }

    def prior(self, transform: str) -> float:
        """A deterministic historical prior in [0, 1] for the NBA engine.

        Blends success rate with normalized average yield. Returns 0.0 when
        learning is disabled or the transform is unseen, so callers degrade to
        their non-learning behaviour.
        """

        s = self.stats(transform)
        if s["runs"] == 0:
            return 0.0
        yield_norm = min(s["avg_yield"] / 5.0, 1.0)  # 5+ entities/success -> max
        return round(0.6 * s["success_rate"] + 0.4 * yield_norm, 3)

    def all_stats(self) -> Dict[str, Dict[str, float]]:
        if not is_enabled():
            return {}
        with self._lock:
            self._ensure_loaded()
            return {t: self.stats(t) for t in sorted(self._data)}

    def flush(self) -> Optional[str]:
        """Persist the store to disk (no-op when disabled). Returns the path."""

        if not is_enabled():
            return None
        path = store_path()
        with self._lock:
            self._ensure_loaded()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        return path

    def reset(self) -> None:
        """Clear the in-memory and on-disk store (no-op when disabled)."""

        if not is_enabled():
            return
        with self._lock:
            self._data = {}
            path = store_path()
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def reload(self) -> None:
        """Force a re-read on next access (used by tests when the path changes)."""

        self._loaded_from = None


#: Process-wide learning store.
store = LearningStore()
