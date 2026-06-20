from __future__ import annotations


def accessory_rows_for_view(analysis: object | None) -> list[object]:
    if analysis is None:
        return []
    view = str(getattr(analysis, "view", "recommended") or "recommended").lower().replace("_", "-")
    if view in {"craftable-now", "craftable"}:
        return list(getattr(analysis, "craftable", []) or [])
    if view in {"available-on-ah", "buy-from-ah", "buy-ah", "ah"}:
        return list(getattr(analysis, "ah_available", []) or [])
    if view == "upgrades":
        return list(getattr(analysis, "upgrades", []) or [])
    if view == "locked":
        return list(getattr(analysis, "locked", []) or [])
    if view in {"all-missing", "missing"}:
        return list(getattr(analysis, "all_missing", []) or [])
    if view in {"owned", "owned-covered", "owned--covered"}:
        return list(getattr(analysis, "owned", []) or [])
    if view == "details":
        return list(getattr(analysis, "rows", []) or [])
    return list(getattr(analysis, "recommendations", []) or [])
