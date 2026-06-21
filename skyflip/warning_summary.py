from __future__ import annotations

import re
from dataclasses import dataclass, field


_SKYCOFL_WARNING_RE = re.compile(r"^SkyCofl (?P<operation>.+?) unavailable for (?P<tag>[^:]+): (?P<error>.+)$")


@dataclass
class _WarningGroup:
    label: str
    count: int = 0
    examples: list[str] = field(default_factory=list)

    def add(self, tag: str) -> None:
        self.count += 1
        if tag not in self.examples and len(self.examples) < 3:
            self.examples.append(tag)


def compact_warnings(warnings: list[str]) -> list[str]:
    seen: set[str] = set()
    regular: list[str] = []
    skycofl_groups: dict[str, _WarningGroup] = {}

    for warning in warnings:
        text = str(warning).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        match = _SKYCOFL_WARNING_RE.match(text)
        if not match:
            regular.append(text)
            continue
        key, label = _skycofl_group(match.group("error"))
        group = skycofl_groups.setdefault(key, _WarningGroup(label))
        group.add(match.group("tag"))

    return [*regular, *[_format_group(group) for group in skycofl_groups.values()]]


def _skycofl_group(error: str) -> tuple[str, str]:
    lowered = error.lower()
    if "429" in lowered:
        return "skycofl-rate-limited", "SkyCofl rate limited market checks"
    if "400" in lowered or "bad request" in lowered:
        return "skycofl-rejected", "SkyCofl rejected unsupported market checks"
    return "skycofl-unavailable", "SkyCofl market checks unavailable"


def _format_group(group: _WarningGroup) -> str:
    examples = f"; examples: {', '.join(group.examples)}" if group.examples else ""
    return f"{group.label}: {group.count} check{'s' if group.count != 1 else ''}{examples}."
