from __future__ import annotations

import shutil
from dataclasses import dataclass


MIN_TERMINAL_WIDTH = 72
MIN_TERMINAL_HEIGHT = 18
NARROW_TERMINAL_WIDTH = 90
MAX_TERMINAL_WIDTH = 140
DEFAULT_TERMINAL_WIDTH = 96
DEFAULT_TERMINAL_HEIGHT = 24


@dataclass(frozen=True)
class TerminalSize:
    width: int
    height: int

    @property
    def too_small(self) -> bool:
        return self.width < MIN_TERMINAL_WIDTH or self.height < MIN_TERMINAL_HEIGHT

    @property
    def narrow(self) -> bool:
        return not self.too_small and self.width < NARROW_TERMINAL_WIDTH


def get_terminal_size() -> TerminalSize:
    size = shutil.get_terminal_size((DEFAULT_TERMINAL_WIDTH, DEFAULT_TERMINAL_HEIGHT))
    return TerminalSize(width=max(1, size.columns), height=max(1, size.lines))


def usable_width(size: TerminalSize | None = None) -> int:
    size = size or get_terminal_size()
    return max(1, min(MAX_TERMINAL_WIDTH, size.width))


def too_small_message(size: TerminalSize | None = None) -> str:
    size = size or get_terminal_size()
    return (
        f"Terminal is too small ({size.width}x{size.height}). "
        f"Please enlarge it to at least {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}."
    )


def clip_text(value: object, width: int) -> str:
    text = str(value)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def compact_line(value: str, *, width: int | None = None) -> str:
    return clip_text(value, width or usable_width())


def format_table(
    headers: list[str],
    rows: list[list[object]],
    *,
    size: TerminalSize | None = None,
    essential_columns: set[int] | None = None,
) -> list[str]:
    size = size or get_terminal_size()
    if size.too_small:
        return [too_small_message(size)]
    width = usable_width(size)
    normalized_rows = [[str(value) for value in row] for row in rows]
    selected = _selected_columns(headers, normalized_rows, width, essential_columns or set())
    visible_headers = [headers[index] for index in selected]
    visible_rows = [[row[index] if index < len(row) else "" for index in selected] for row in normalized_rows]
    widths = _column_widths(visible_headers, visible_rows, width)
    lines = [
        " | ".join(clip_text(header, widths[index]).ljust(widths[index]) for index, header in enumerate(visible_headers)).rstrip(),
        "-+-".join("-" * column_width for column_width in widths).rstrip(),
    ]
    for row in visible_rows:
        lines.append(" | ".join(clip_text(value, widths[index]).ljust(widths[index]) for index, value in enumerate(row)).rstrip())
    hidden = len(headers) - len(selected)
    if hidden > 0:
        lines.append(clip_text(f"... {hidden} column(s) hidden; enlarge terminal for full table", width))
    return lines


def _selected_columns(headers: list[str], rows: list[list[str]], width: int, essential_columns: set[int]) -> list[int]:
    selected = list(range(len(headers)))
    while len(selected) > 1 and _table_width(headers, rows, selected) > width:
        removable = [index for index in selected if index not in essential_columns]
        if not removable:
            removable = selected[1:]
        selected.remove(removable[-1])
    return selected


def _column_widths(headers: list[str], rows: list[list[str]], width: int) -> list[int]:
    if not headers:
        return []
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    widths = [min(32, max(3, column_width)) for column_width in widths]
    separator_width = max(0, (len(widths) - 1) * 3)
    available = max(len(widths) * 3, width - separator_width)
    while sum(widths) > available:
        largest = max(range(len(widths)), key=lambda index: widths[index])
        if widths[largest] <= 3:
            break
        widths[largest] -= 1
    return widths


def _table_width(headers: list[str], rows: list[list[str]], selected: list[int]) -> int:
    widths = [len(headers[index]) for index in selected]
    for row in rows:
        for output_index, source_index in enumerate(selected):
            widths[output_index] = min(32, max(widths[output_index], len(row[source_index]) if source_index < len(row) else 0))
    return sum(widths) + max(0, len(widths) - 1) * 3
