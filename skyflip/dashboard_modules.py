from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardModule:
    key: str
    title: str
    sections: tuple[str, ...]
    summary_sections: tuple[str, ...] = ("summary",)

    @property
    def result_sections(self) -> tuple[str, ...]:
        return (*self.summary_sections, *self.sections, "warnings", "rejected")


DASHBOARD_MODULES: tuple[DashboardModule, ...] = (
    DashboardModule(
        key="bazaar",
        title="Bazaar Flip",
        sections=("bazaar-spread", "bazaar-order"),
    ),
    DashboardModule(
        key="craft",
        title="AH Craft Flips",
        sections=("craft",),
    ),
    DashboardModule(
        key="accessories",
        title="Accessories Helper",
        sections=("talisman",),
    ),
    DashboardModule(
        key="compression",
        title="Bazaar Compression",
        sections=("bazaar-compression",),
    ),
    DashboardModule(
        key="ah-bin",
        title="AH BIN Finder",
        sections=("ah-underpriced",),
    ),
)

MODULES_BY_KEY = {module.key: module for module in DASHBOARD_MODULES}


def get_dashboard_module(key: str) -> DashboardModule:
    return MODULES_BY_KEY[key]


def module_keys_for_sections(sections: set[str]) -> list[str]:
    return [
        module.key
        for module in DASHBOARD_MODULES
        if any(section in sections for section in module.sections)
    ]
