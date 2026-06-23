import os
import sys
import threading
import time

import pytest

import skyflip.dashboard_menu_ui as menu_ui
from skyflip.dashboard_menu_ui import (
    _clear_line_endings,
    _enter_terminal_app_mode,
    _exit_terminal_app_mode,
    _key_name_from_posix_escape_sequence,
    _pause_with_redraw,
    _read_posix_escape_key,
    _read_key,
    _read_key_with_redraw,
)


def test_redraw_loop_draws_at_frame_interval_until_key():
    now = 0.0
    draws = []
    timeouts = []

    def monotonic():
        return now

    def draw_screen():
        draws.append(monotonic())

    def read_key(*, timeout=None):
        nonlocal now
        timeouts.append(timeout)
        now += timeout or 0.0
        return "enter" if len(timeouts) == 3 else ""

    key = _read_key_with_redraw(draw_screen, frame_rate=4.0, read_key=read_key, monotonic=monotonic)

    assert key == "enter"
    assert draws == [0.0, 0.25, 0.5]
    assert timeouts == [0.25, 0.25, 0.25]


def test_redraw_loop_returns_without_extra_draw_when_key_arrives():
    draws = []

    def draw_screen():
        draws.append("draw")

    def read_key(*, timeout=None):
        return "q"

    key = _read_key_with_redraw(draw_screen, frame_rate=8.0, read_key=read_key, monotonic=lambda: 0.0)

    assert key == "q"
    assert draws == ["draw"]


def test_redraw_loop_writes_buffered_frame_without_physical_clear(capsys):
    def draw_screen():
        menu_ui._clear_screen()
        print("Frame body")

    def read_key(*, timeout=None):
        return "q"

    key = _read_key_with_redraw(draw_screen, frame_rate=8.0, read_key=read_key, monotonic=lambda: 0.0)

    output = capsys.readouterr().out
    assert key == "q"
    assert "Frame body" in output
    assert "\033[H" in output
    assert "\033[2J" not in output
    assert output.endswith("\033[?25h")


def test_redraw_loop_keeps_cursor_hidden_inside_app_mode(monkeypatch, capsys):
    monkeypatch.setattr(menu_ui, "_TERMINAL_APP_MODE_DEPTH", 1)

    def read_key(*, timeout=None):
        return "q"

    key = _read_key_with_redraw(lambda: print("Frame body"), frame_rate=8.0, read_key=read_key, monotonic=lambda: 0.0)

    output = capsys.readouterr().out
    assert key == "q"
    assert "Frame body" in output
    assert not output.endswith("\033[?25h")


def test_terminal_app_mode_uses_alternate_screen(monkeypatch, capsys):
    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(menu_ui, "_TERMINAL_APP_MODE_DEPTH", 0)

    enabled = _enter_terminal_app_mode()
    _exit_terminal_app_mode(enabled)

    output = capsys.readouterr().out
    assert enabled
    assert "\033[?1049h" in output
    assert output.endswith("\033[?25h\033[?1049l")


def test_terminal_app_mode_can_be_disabled(monkeypatch, capsys):
    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setenv("SKYFLIP_NO_ALT_SCREEN", "1")

    enabled = _enter_terminal_app_mode()
    _exit_terminal_app_mode(enabled)

    assert not enabled
    assert capsys.readouterr().out == ""


def test_clear_line_endings_prevents_stale_characters():
    assert _clear_line_endings("short\nlast") == "short\033[K\nlast\033[K"


def test_posix_escape_parser_handles_standard_arrows():
    assert _key_name_from_posix_escape_sequence("[A") == "up"
    assert _key_name_from_posix_escape_sequence("[B") == "down"
    assert _key_name_from_posix_escape_sequence("[C") == "right"
    assert _key_name_from_posix_escape_sequence("[D") == "left"


def test_posix_escape_parser_handles_application_cursor_arrows():
    assert _key_name_from_posix_escape_sequence("OA") == "up"
    assert _key_name_from_posix_escape_sequence("OB") == "down"
    assert _key_name_from_posix_escape_sequence("OC") == "right"
    assert _key_name_from_posix_escape_sequence("OD") == "left"


def test_posix_escape_parser_handles_modified_arrows():
    assert _key_name_from_posix_escape_sequence("[1;2A") == "up"
    assert _key_name_from_posix_escape_sequence("[1;5B") == "down"
    assert _key_name_from_posix_escape_sequence("[1;3C") == "right"
    assert _key_name_from_posix_escape_sequence("[1;4D") == "left"


def test_posix_escape_parser_keeps_escape_separate_from_unknown_sequences():
    assert _key_name_from_posix_escape_sequence("") == "escape"
    assert _key_name_from_posix_escape_sequence("[200~") == ""
    assert _key_name_from_posix_escape_sequence("x") == ""


def test_posix_escape_reader_reads_from_file_descriptor():
    chars = iter("[C")
    selected_fds = []

    def select_fn(readers, writers, errors, timeout):
        selected_fds.append(readers[0])
        return ([readers[0]], writers, errors)

    def read_char(fd):
        assert fd == 123
        return next(chars)

    assert _read_posix_escape_key(123, select_fn, read_char) == "right"
    assert selected_fds == [123, 123]


def test_posix_escape_reader_returns_escape_when_sequence_does_not_arrive():
    def select_fn(readers, writers, errors, timeout):
        return ([], writers, errors)

    assert _read_posix_escape_key(123, select_fn, lambda fd: "") == "escape"


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "openpty"), reason="requires a POSIX pty")
def test_read_key_handles_posix_arrow_sequences_from_pty():
    original_stdin = sys.stdin
    cases = [
        (b"\x1b[A", "up"),
        (b"\x1b[B", "down"),
        (b"\x1b[C", "right"),
        (b"\x1b[D", "left"),
        (b"\x1bOA", "up"),
    ]

    for sequence, expected in cases:
        master, slave = os.openpty()
        stdin = os.fdopen(slave, "r", encoding="utf-8", buffering=1)
        try:
            sys.stdin = stdin

            def write_sequence():
                time.sleep(0.01)
                os.write(master, sequence)

            writer = threading.Thread(target=write_sequence)
            writer.start()
            assert _read_key(timeout=0.5) == expected
            writer.join(timeout=0.5)
        finally:
            sys.stdin = original_stdin
            stdin.close()
            os.close(master)


def test_select_menu_uses_redraw_loop_in_interactive_mode(monkeypatch, capsys):
    redraw_calls = []

    def fake_redraw_loop(draw_screen):
        redraw_calls.append("called")
        draw_screen()
        return "enter"

    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(menu_ui, "_read_key_with_redraw", fake_redraw_loop)

    choice = menu_ui._select_menu(
        "Test",
        [("1", "First action", "hint")],
        args=None,
        state=None,
        prompt="Choose",
    )

    assert choice == "1"
    assert redraw_calls == ["called"]
    assert "First action" in capsys.readouterr().out


def test_select_menu_handles_page_navigation_in_interactive_mode(monkeypatch):
    keys = iter(["page_down", "enter"])

    def fake_redraw_loop(draw_screen):
        draw_screen()
        return next(keys)

    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(menu_ui, "_read_key_with_redraw", fake_redraw_loop)

    choice = menu_ui._select_menu(
        "Test",
        [(str(index), f"Action {index}", "hint") for index in range(1, 8)],
        args=None,
        state=None,
        prompt="Choose",
    )

    assert choice == "4"


def test_select_menu_handles_home_and_end_in_interactive_mode(monkeypatch):
    keys = iter(["end", "home", "enter"])

    def fake_redraw_loop(draw_screen):
        draw_screen()
        return next(keys)

    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(menu_ui, "_read_key_with_redraw", fake_redraw_loop)

    choice = menu_ui._select_menu(
        "Test",
        [("1", "First action", "hint"), ("2", "Second action", "hint")],
        args=None,
        state=None,
        prompt="Choose",
    )

    assert choice == "1"


def test_pause_with_redraw_uses_redraw_loop_in_interactive_mode(monkeypatch):
    redraw_calls = []

    def fake_redraw_loop(draw_screen):
        redraw_calls.append("called")
        draw_screen()
        return "enter"

    monkeypatch.setattr(menu_ui, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(menu_ui, "_read_key_with_redraw", fake_redraw_loop)

    _pause_with_redraw(lambda: redraw_calls.append("draw"))

    assert redraw_calls == ["called", "draw"]
