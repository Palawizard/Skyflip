from skyflip.terminal_layout import (
    MIN_TERMINAL_HEIGHT,
    MIN_TERMINAL_WIDTH,
    TerminalSize,
    clip_text,
    format_table,
    too_small_message,
)


def test_too_small_terminal_returns_resize_message():
    size = TerminalSize(MIN_TERMINAL_WIDTH - 1, MIN_TERMINAL_HEIGHT)

    lines = format_table(["A", "B"], [["one", "two"]], size=size)

    assert lines == [too_small_message(size)]


def test_narrow_terminal_hides_secondary_columns():
    size = TerminalSize(72, 24)
    headers = ["#", "Item", "Profit", "Margin", "Speed", "Confidence", "Notes"]
    rows = [["1", "Very Long Market Candidate Name", "123k", "12.5%", "Medium", "87%", "manual verification needed"]]

    lines = format_table(headers, rows, size=size, essential_columns={0, 1})

    assert all(len(line) <= size.width for line in lines)
    assert any("column(s) hidden" in line for line in lines)
    assert "Very Long Market" in "\n".join(lines)


def test_wide_terminal_keeps_all_columns():
    size = TerminalSize(132, 30)
    headers = ["#", "Item", "Profit", "Margin", "Speed", "Confidence", "Notes"]
    rows = [["1", "Very Long Market Candidate Name", "123k", "12.5%", "Medium", "87%", "manual verification needed"]]

    lines = format_table(headers, rows, size=size, essential_columns={0, 1})

    assert all(len(line) <= size.width for line in lines)
    assert not any("column(s) hidden" in line for line in lines)
    assert "Confidence" in lines[0]
    assert "Notes" in lines[0]


def test_clip_text_never_exceeds_width():
    assert clip_text("abcdefgh", 5) == "ab..."
    assert len(clip_text("abcdefgh", 3)) == 3
