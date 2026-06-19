from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RejectedItem:
    section: str
    item: str
    reason: str


@dataclass(frozen=True)
class SectionResult:
    name: str
    rows: list[object] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
