import skyflip.dashboard_menu_ui as menu_ui
from skyflip.dashboard_menu_ui import (
    _clear_line_endings,
    _enter_terminal_app_mode,
    _exit_terminal_app_mode,
    _pause_with_redraw,
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
