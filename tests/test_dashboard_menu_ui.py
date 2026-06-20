import skyflip.dashboard_menu_ui as menu_ui
from skyflip.dashboard_menu_ui import _read_key_with_redraw


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
