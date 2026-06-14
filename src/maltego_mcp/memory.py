"""Investigation Memory -- procedural memory for an investigation.

Where the graph captures *what was found*, the Investigation Memory captures
*how and why it was found*: every transform execution, the entity that triggered
it, the reason it was chosen, what it discovered, whether it succeeded, how
valuable it was, and whether it is worth reconsidering later.

Design goals:

* **Separate from the graph structure** -- memory lives in its own object and is
  serialized to a separate member inside the ``.mtgx`` archive, so it never
  affects Maltego CE compatibility. It keeps *strong references* to the graph by
  storing entity ids.
* **Queryable** -- by step, by execution id, by entity, by transform.
* **Self-contained** -- this module imports nothing from the graph/transform
  layers, avoiding circular imports. Importance/novelty scores are computed by
  callers (the scoring engine) and passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

MEMORY_SCHEMA_VERSION = 1

# Step status values.
STATUS_SUCCESS = "success"   # ran and produced at least one new entity
STATUS_EMPTY = "empty"       # ran cleanly but produced nothing new
STATUS_ERROR = "error"       # raised / provider error / missing key


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class InvestigationStep:
    """One recorded transform execution (procedural-memory entry)."""

    step: int
    execution_id: str
    timestamp: str
    trigger_entity_id: str
    trigger_entity_value: str
    transform: str
    provider: str
    reason: str
    new_entity_ids: List[str] = field(default_factory=list)
    new_entities: int = 0
    importance_score: float = 0.0
    status: str = STATUS_SUCCESS
    reconsider: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "trigger_entity": self.trigger_entity_id,
            "trigger_entity_value": self.trigger_entity_value,
            "transform": self.transform,
            "provider": self.provider,
            "reason": self.reason,
            "new_entity_ids": list(self.new_entity_ids),
            "new_entities": self.new_entities,
            "importance_score": self.importance_score,
            "status": self.status,
            "reconsider": self.reconsider,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InvestigationStep":
        return cls(
            step=int(d.get("step", 0)),
            execution_id=str(d.get("execution_id", "")),
            timestamp=str(d.get("timestamp", "")),
            trigger_entity_id=str(d.get("trigger_entity", "")),
            trigger_entity_value=str(d.get("trigger_entity_value", "")),
            transform=str(d.get("transform", "")),
            provider=str(d.get("provider", "")),
            reason=str(d.get("reason", "")),
            new_entity_ids=list(d.get("new_entity_ids", []) or []),
            new_entities=int(d.get("new_entities", 0)),
            importance_score=float(d.get("importance_score", 0.0)),
            status=str(d.get("status", STATUS_SUCCESS)),
            reconsider=bool(d.get("reconsider", False)),
            message=str(d.get("message", "")),
        )


class InvestigationMemory:
    """Ordered, queryable log of investigation steps for one graph."""

    def __init__(self) -> None:
        self._steps: List[InvestigationStep] = []
        self._by_exec: Dict[str, InvestigationStep] = {}
        self._exec_counter: int = 0

    # -- recording ------------------------------------------------------------
    def next_execution_id(self) -> str:
        eid = f"x{self._exec_counter}"
        self._exec_counter += 1
        return eid

    def record(
        self,
        *,
        trigger_entity_id: str,
        trigger_entity_value: str,
        transform: str,
        provider: str,
        reason: str,
        new_entity_ids: Optional[List[str]] = None,
        importance_score: float = 0.0,
        status: str = STATUS_SUCCESS,
        reconsider: bool = False,
        message: str = "",
        timestamp: Optional[str] = None,
    ) -> InvestigationStep:
        """Append a step and return it. ``step`` numbers are 1-based."""

        new_ids = list(new_entity_ids or [])
        step = InvestigationStep(
            step=len(self._steps) + 1,
            execution_id=self.next_execution_id(),
            timestamp=timestamp or _now_iso(),
            trigger_entity_id=trigger_entity_id,
            trigger_entity_value=trigger_entity_value,
            transform=transform,
            provider=provider,
            reason=reason,
            new_entity_ids=new_ids,
            new_entities=len(new_ids),
            importance_score=round(float(importance_score), 2),
            status=status,
            reconsider=reconsider,
            message=message,
        )
        self._steps.append(step)
        self._by_exec[step.execution_id] = step
        return step

    # -- queries --------------------------------------------------------------
    @property
    def steps(self) -> List[InvestigationStep]:
        return list(self._steps)

    def is_empty(self) -> bool:
        return not self._steps

    def get(self, execution_id: str) -> Optional[InvestigationStep]:
        return self._by_exec.get(execution_id)

    def timeline(self) -> List[InvestigationStep]:
        """Steps in chronological (execution) order."""

        return list(self._steps)

    def steps_for_trigger(self, entity_id: str) -> List[InvestigationStep]:
        """Steps that were triggered *by* the given entity."""

        return [s for s in self._steps if s.trigger_entity_id == entity_id]

    def discovering_steps(self, entity_id: str) -> List[InvestigationStep]:
        """Steps that *discovered* (added) the given entity."""

        return [s for s in self._steps if entity_id in s.new_entity_ids]

    def transforms_run_on(self, entity_id: str) -> set:
        """Set of transform names already executed against an entity."""

        return {s.transform for s in self.steps_for_trigger(entity_id)}

    def sources_for_entity(self, entity_id: str) -> List[str]:
        """Distinct providers whose transforms discovered the entity (sorted)."""

        return sorted({s.provider for s in self.discovering_steps(entity_id)})

    def provider_yield(self, provider: str) -> List[int]:
        """List of ``new_entities`` counts for each successful step by a provider."""

        return [s.new_entities for s in self._steps if s.provider == provider]

    def transform_attempts(self, transform: str) -> int:
        return sum(1 for s in self._steps if s.transform == transform)

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "steps": [s.to_dict() for s in self._steps],
        }

    def load_dict(self, data: dict) -> None:
        """Replace contents from a serialized dict (used by the .mtgx reader)."""

        self._steps = []
        self._by_exec = {}
        self._exec_counter = 0
        for raw in data.get("steps", []) or []:
            step = InvestigationStep.from_dict(raw)
            self._steps.append(step)
            self._by_exec[step.execution_id] = step
            # Keep the counter ahead of any loaded execution id like "x12".
            if step.execution_id.startswith("x") and step.execution_id[1:].isdigit():
                self._exec_counter = max(self._exec_counter, int(step.execution_id[1:]) + 1)

    def remap_and_extend(self, other: "InvestigationMemory", id_map: Dict[str, str]) -> int:
        """Append ``other``'s steps, remapping entity ids via ``id_map``.

        Used when merging investigations. Returns the number of steps appended.
        """

        count = 0
        for s in other.timeline():
            self.record(
                trigger_entity_id=id_map.get(s.trigger_entity_id, s.trigger_entity_id),
                trigger_entity_value=s.trigger_entity_value,
                transform=s.transform,
                provider=s.provider,
                reason=s.reason,
                new_entity_ids=[id_map.get(i, i) for i in s.new_entity_ids],
                importance_score=s.importance_score,
                status=s.status,
                reconsider=s.reconsider,
                message=s.message,
                timestamp=s.timestamp,
            )
            count += 1
        return count
