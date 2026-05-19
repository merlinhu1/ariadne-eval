from __future__ import annotations

from typing import Iterable, Protocol


class AgentAdapter(Protocol):
    framework_name: str

    def discover_due_sources(self, since: float | None = None) -> Iterable[str]:
        """Return source ids, e.g. Hermes session ids."""

    def load_source(self, source_id: str) -> dict:
        """Load raw framework-specific session/run data."""

    def normalize_eval_units(self, raw_source: dict) -> list[dict]:
        """Return normalized eval-unit dictionaries."""

    def load_trace_events(self, eval_unit_id: str) -> list[dict]:
        """Return normalized event dictionaries."""
