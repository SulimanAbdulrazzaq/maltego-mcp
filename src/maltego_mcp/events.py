"""Architecture-agnostic event bus for optional real-time investigation mode.

The bus is a tiny publish/subscribe hub plus a bounded ring buffer of recent
events. It deliberately knows nothing about MCP, transports, or the graph -- any
component can ``emit`` an event and any consumer (an MCP tool polling
``recent``, a future websocket/SSE bridge, a UI) can read or subscribe.

Real-time mode is **optional and side-effect-free for existing flows**: emitting
is always cheap (append to a deque + call any registered callbacks), and nothing
in the core depends on anyone listening. Existing ``.mtgx`` behaviour is
unchanged.

Event types currently emitted:

* ``entity_discovered``      -- a new entity was added by a transform
* ``transform_started``      -- a transform began executing
* ``transform_completed``    -- a transform finished (success/empty/error)
* ``report_generated``       -- a report was produced
* ``recommendation_updated`` -- next-best-actions were recomputed
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional

# Known event type constants (string values are part of the public contract).
ENTITY_DISCOVERED = "entity_discovered"
TRANSFORM_STARTED = "transform_started"
TRANSFORM_COMPLETED = "transform_completed"
REPORT_GENERATED = "report_generated"
RECOMMENDATION_UPDATED = "recommendation_updated"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class Event:
    """A single investigation event."""

    seq: int
    type: str
    timestamp: str
    data: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }


EventCallback = Callable[[Event], None]


class EventBus:
    """Pub/sub hub with a bounded buffer of recent events."""

    def __init__(self, maxlen: int = 500) -> None:
        self._buffer: Deque[Event] = deque(maxlen=maxlen)
        self._subscribers: Dict[str, EventCallback] = {}
        self._seq: int = 0
        # When False, callbacks are not invoked (buffer still records, cheaply).
        self.live: bool = False

    def emit(self, event_type: str, data: Optional[Dict[str, object]] = None) -> Event:
        """Record an event and notify subscribers. Returns the created Event."""

        event = Event(
            seq=self._seq,
            type=event_type,
            timestamp=_now_iso(),
            data=dict(data or {}),
        )
        self._seq += 1
        self._buffer.append(event)
        if self.live:
            for cb in list(self._subscribers.values()):
                try:
                    cb(event)
                except Exception:  # noqa: BLE001 - a bad subscriber must not break emit
                    continue
        return event

    def subscribe(self, callback: Optional[EventCallback] = None) -> str:
        """Register a subscriber, enable live mode, and return a subscription id."""

        sub_id = f"sub{len(self._subscribers)}"
        self._subscribers[sub_id] = callback or (lambda e: None)
        self.live = True
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        existed = self._subscribers.pop(sub_id, None) is not None
        if not self._subscribers:
            self.live = False
        return existed

    def recent(self, limit: int = 50, since_seq: Optional[int] = None) -> List[Event]:
        """Return up to ``limit`` recent events, optionally with seq > since_seq."""

        events = list(self._buffer)
        if since_seq is not None:
            events = [e for e in events if e.seq > since_seq]
        return events[-limit:]

    def clear(self) -> None:
        """Reset the buffer and sequence (used by tests)."""

        self._buffer.clear()
        self._seq = 0

    @property
    def next_seq(self) -> int:
        return self._seq

    def subscriber_count(self) -> int:
        return len(self._subscribers)


#: Process-wide event bus.
bus = EventBus()
