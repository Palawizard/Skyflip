from __future__ import annotations

import argparse
import io
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

from .dashboard import DEFAULT_SECTIONS
from .accessory_views import accessory_rows_for_view
from .terminal_layout import TerminalSize, clip_text, get_terminal_size, too_small_message, usable_width
from .user_config import load_user_config


SECTION_LABELS = {
    "summary": "Player summary",
    "craft": "Craft flips",
    "bazaar-spread": "Bazaar spread flips",
    "bazaar-order": "Bazaar order flips",
    "bazaar-compression": "Bazaar compression/decompression",
    "ah-underpriced": "AH BIN underpriced finder",
    "talisman": "Accessories Helper",
    "warnings": "Warnings",
    "rejected": "Rejected",
}

TERMINAL_REDRAW_FPS = 8.0
_KEY_POLL_SECONDS = 0.01
_ESCAPE_SEQUENCE_SECONDS = 0.05
_MAX_ESCAPE_SEQUENCE_CHARS = 16
_REDRAW_CAPTURE_DEPTH = 0
_TERMINAL_APP_MODE_DEPTH = 0
_ARROW_KEY_NAMES = {"A": "up", "B": "down", "C": "right", "D": "left"}
_CONTROL_SEQUENCE_KEY_NAMES = {
    "H": "home",
    "F": "end",
    "1~": "home",
    "4~": "end",
    "5~": "page_up",
    "6~": "page_down",
    "7~": "home",
    "8~": "end",
}


def _parse_sections(value: str) -> list[str]:
    aliases = {"spread": "bazaar-spread"}
    return [aliases.get(part.strip(), part.strip()) for part in value.split(",") if part.strip()]


def _section_summary(value: str) -> str:
    parts = _parse_sections(value)
    if set(parts) == set(DEFAULT_SECTIONS):
        return "all"
    return ", ".join(parts) or "none"


def _section_name(key: str) -> str:
    names = {
        "summary": "Player summary",
        "warnings": "Warnings",
        "rejected": "Rejected",
    }
    return names.get(key, key)


def _section_hint(key: str) -> str:
    hints = {
        "summary": "profile, budget, progression",
        "craft": "manual craft/list candidates",
        "bazaar-spread": "buy-order to sell-offer spread",
        "bazaar-order": "simpler Bazaar order flips",
        "bazaar-compression": "manual compress/decompress flips",
        "ah-underpriced": "manual BIN checks",
        "talisman": "missing accessories and Magical Power",
        "warnings": "API/data issues",
        "rejected": "filtered candidates",
    }
    return hints.get(key, "")


def _section_count(data, key: str) -> int | str:
    if key == "summary":
        return "-"
    if key == "craft":
        return len(getattr(data, "craft", []) or [])
    if key == "bazaar-spread":
        return len(getattr(data, "bazaar_spreads", []) or [])
    if key == "bazaar-order":
        return len(getattr(data, "bazaar_orders", []) or [])
    if key == "bazaar-compression":
        return len(getattr(data, "conversions", []) or [])
    if key == "ah-underpriced":
        return len(getattr(data, "ah_underpriced", []) or [])
    if key == "talisman":
        return len(accessory_rows_for_view(getattr(data, "talisman_helper", None)))
    if key == "warnings":
        return len(getattr(data, "warnings", []) or [])
    if key == "rejected":
        return len(getattr(data, "rejected", []) or [])
    return 0


def _clear_screen() -> None:
    if _REDRAW_CAPTURE_DEPTH:
        return
    if os.environ.get("SKYFLIP_NO_CLEAR"):
        print()
        return
    if os.name == "nt":
        os.system("cls")
    print("\033[H\033[2J\033[3J", end="", flush=True)


def _width() -> int:
    return usable_width()


def _terminal_size() -> TerminalSize:
    return get_terminal_size()


def _draw_too_small_if_needed(size: TerminalSize | None = None) -> bool:
    size = size or _terminal_size()
    if not size.too_small:
        return False
    print(too_small_message(size))
    return True


def _draw_header(title: str, args: argparse.Namespace, state: _MenuState | None) -> None:
    size = _terminal_size()
    width = usable_width(size)
    print("=" * width)
    print(clip_text(f"skyflip / {title}", width).ljust(width))
    print("-" * width)
    profile = _header_profile_label(args, state)
    player = _header_player_label(args, state)
    budget = _coins(args.budget) if args.budget is not None else "not set"
    refresh = state.last_refresh if state and state.last_refresh else "never"
    auto = "ON" if state and state.auto_refresh else "OFF"
    preset = getattr(args, "active_settings_profile", None) or "default / unsaved"
    print(compact_menu_line(f"Profile: {profile}", width))
    print(compact_menu_line(f"Player:  {player}  Budget: {budget}  Last refresh: {refresh}  Auto: {auto}", width))
    print(compact_menu_line(f"Preset:  {preset}", width))
    if state and state.status_message:
        print(compact_menu_line(f"Status: {state.status_message}", width))
    print("=" * width)
    print()


def _header_profile_label(args: argparse.Namespace, state: _MenuState | None) -> str:
    loaded_profile = getattr(getattr(state, "latest", None), "profile", None)
    profile_name = getattr(loaded_profile, "profile_name", None)
    if profile_name:
        return str(profile_name)
    if getattr(args, "profile_file", None):
        return _short_path(args.profile_file)
    config = load_user_config()
    if config and config.selected_profile_name:
        return config.selected_profile_name
    return "not set"


def _header_player_label(args: argparse.Namespace, state: _MenuState | None) -> str:
    loaded_profile = getattr(getattr(state, "latest", None), "profile", None)
    player_name = getattr(loaded_profile, "player_name", None)
    if player_name:
        return str(player_name)
    return args.player_name or "not set"


def _draw_simple_header(title: str) -> None:
    width = _width()
    print("=" * width)
    print(compact_menu_line(f"skyflip / {title}", width))
    print("=" * width)
    print()


def _draw_counts(data, count_sections: tuple[str, ...] | None = None) -> None:
    if data is None:
        return
    if count_sections is None:
        rows = [
            ("Craft", len(data.craft)),
            ("Spread", len(data.bazaar_spreads)),
            ("Order", len(data.bazaar_orders)),
            ("Compression", len(data.conversions)),
            ("AH", len(data.ah_underpriced)),
            ("Accessories", len(accessory_rows_for_view(getattr(data, "talisman_helper", None)))),
            ("Warnings", len(data.warnings)),
        ]
    else:
        rows = [
            (SECTION_LABELS.get(key, key).replace(" flips", "").replace(" finder", ""), _section_count(data, key))
            for key in count_sections
        ]
    print(compact_menu_line("Results  " + "  ".join(f"{name}: {_badge(str(count))}" for name, count in rows), _width()))
    print()


def _draw_menu(items: list[tuple[str, str, str]]) -> None:
    width = _width()
    for key, label, hint in items:
        print(compact_menu_line(f"  {key.rjust(2)}  {label.ljust(26)} {hint}", width))
    print()


def _draw_settings(items: list[tuple[str, str, str]]) -> None:
    width = _width()
    label_width = max(len(label) for _, label, _ in items)
    for key, label, value in items:
        print(compact_menu_line(f"  {key.rjust(2)}  {label.ljust(label_width)}  {value}", width))
    print()


def _select_menu(
    title: str,
    entries: list[tuple[str, str, str]],
    *,
    args: argparse.Namespace | None,
    state: _MenuState | None,
    prompt: str,
    show_counts: bool = False,
    count_sections: tuple[str, ...] | None = None,
    note: str | None = None,
) -> str:
    if not _interactive_menu_enabled():
        _clear_screen()
        _draw_too_small_if_needed()
        if args is not None:
            _draw_header(title, args, state)
        else:
            _draw_simple_header(title)
        if note:
            print(note)
            print()
        if show_counts and state is not None:
            _draw_counts(state.latest, count_sections=count_sections)
        _draw_menu(entries)
        return input(compact_menu_line(f"{prompt}: ", _width())).strip().lower()

    selected = 0
    while True:
        def draw_screen() -> None:
            _clear_screen()
            _draw_too_small_if_needed()
            if args is not None:
                _draw_header(title, args, state)
            else:
                _draw_simple_header(title)
            if note:
                print(note)
                print()
            if show_counts and state is not None:
                _draw_counts(state.latest, count_sections=count_sections)
            _draw_selectable_entries(entries, selected)
            print()
            print(_muted(compact_menu_line("Up/Down move   Enter select   R refresh   Esc back   Q quit/back", _width())))

        key = _read_key_with_redraw(draw_screen)
        if key == "up":
            selected = (selected - 1) % len(entries)
        elif key == "down":
            selected = (selected + 1) % len(entries)
        elif key == "enter":
            return entries[selected][0].lower()
        elif key in {"escape", "q"}:
            return "q" if any(entry[0].lower() == "q" for entry in entries) else "b"
        elif key == "r":
            return "r"
        else:
            for entry_key, _, _ in entries:
                if key == entry_key.lower():
                    return entry_key.lower()


def _draw_selectable_entries(entries: list[tuple[str, str, str]], selected: int) -> None:
    width = _width()
    label_width = min(48, max(len(_plain(label)) for _, label, _ in entries))
    for index, (_, label, hint) in enumerate(entries):
        cursor = ">" if index == selected else " "
        line = compact_menu_line(f" {cursor} {label.ljust(label_width)}  {_muted(hint)}", width)
        if index == selected:
            print(_highlight(line))
        else:
            print(line)


def _interactive_menu_enabled() -> bool:
    if os.environ.get("SKYFLIP_SIMPLE_MENU"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _enter_terminal_app_mode() -> bool:
    if not _interactive_menu_enabled() or os.environ.get("SKYFLIP_NO_ALT_SCREEN"):
        return False
    global _TERMINAL_APP_MODE_DEPTH
    if _TERMINAL_APP_MODE_DEPTH == 0:
        sys.stdout.write("\033[?1049h\033[?25l\033[H\033[J")
        sys.stdout.flush()
    _TERMINAL_APP_MODE_DEPTH += 1
    return True


def _exit_terminal_app_mode(enabled: bool) -> None:
    if not enabled:
        return
    global _TERMINAL_APP_MODE_DEPTH
    _TERMINAL_APP_MODE_DEPTH = max(0, _TERMINAL_APP_MODE_DEPTH - 1)
    if _TERMINAL_APP_MODE_DEPTH == 0:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def _read_key(timeout: float | None = None) -> str:
    if os.name == "nt":
        import msvcrt

        if timeout is not None:
            deadline = time.monotonic() + max(0.0, timeout)
            while not msvcrt.kbhit():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                time.sleep(min(_KEY_POLL_SECONDS, remaining))
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
        if char in ("\r", "\n"):
            return "enter"
        if char == "\x1b":
            return "escape"
        return char.lower()

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if timeout is not None:
            ready, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout))
            if not ready:
                return ""
        char = sys.stdin.read(1)
        if char == "\x1b":
            return _read_posix_escape_key(select.select)
        if char in ("\r", "\n"):
            return "enter"
        return char.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_posix_escape_key(select_fn: Callable[..., tuple[list[object], list[object], list[object]]]) -> str:
    sequence = ""
    deadline = time.monotonic() + _ESCAPE_SEQUENCE_SECONDS
    while len(sequence) < _MAX_ESCAPE_SEQUENCE_CHARS:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select_fn([sys.stdin], [], [], remaining)
        if not ready:
            break
        sequence += sys.stdin.read(1)
        if _posix_escape_sequence_complete(sequence):
            break
    return _key_name_from_posix_escape_sequence(sequence)


def _posix_escape_sequence_complete(sequence: str) -> bool:
    if not sequence:
        return False
    if sequence[0] == "[":
        return len(sequence) > 1 and 0x40 <= ord(sequence[-1]) <= 0x7E
    if sequence[0] == "O":
        return len(sequence) > 1
    return True


def _key_name_from_posix_escape_sequence(sequence: str) -> str:
    if not sequence:
        return "escape"
    if sequence[0] == "O" and len(sequence) >= 2:
        final = sequence[-1]
        return _ARROW_KEY_NAMES.get(final) or _CONTROL_SEQUENCE_KEY_NAMES.get(final, "")
    if sequence[0] != "[" or len(sequence) < 2:
        return ""

    final = sequence[-1]
    if final in _ARROW_KEY_NAMES:
        return _ARROW_KEY_NAMES[final]
    payload = sequence[1:]
    return _CONTROL_SEQUENCE_KEY_NAMES.get(payload, "")


def _read_key_with_redraw(
    draw_screen: Callable[[], None],
    *,
    frame_rate: float = TERMINAL_REDRAW_FPS,
    read_key: Callable[..., str] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str:
    read_key = read_key or _read_key
    monotonic = monotonic or time.monotonic
    frame_interval = 1.0 / max(1.0, frame_rate)
    next_frame = 0.0
    rendered = False
    try:
        while True:
            now = monotonic()
            if now >= next_frame:
                _write_redraw_frame(_capture_redraw_frame(draw_screen))
                rendered = True
                next_frame = now + frame_interval
            timeout = max(0.0, next_frame - monotonic())
            key = read_key(timeout=timeout)
            if key:
                return key
    finally:
        if rendered and _TERMINAL_APP_MODE_DEPTH == 0:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


def _capture_redraw_frame(draw_screen: Callable[[], None]) -> str:
    global _REDRAW_CAPTURE_DEPTH
    buffer = io.StringIO()
    _REDRAW_CAPTURE_DEPTH += 1
    try:
        with redirect_stdout(buffer):
            draw_screen()
    finally:
        _REDRAW_CAPTURE_DEPTH -= 1
    return buffer.getvalue()


def _write_redraw_frame(frame: str) -> None:
    sys.stdout.write("\033[?25l\033[H")
    sys.stdout.write(_clear_line_endings(frame))
    sys.stdout.write("\033[J")
    sys.stdout.flush()


def _clear_line_endings(frame: str) -> str:
    if not frame:
        return ""
    parts = frame.splitlines(keepends=True)
    output = []
    for part in parts:
        if part.endswith("\n"):
            output.append(f"{part[:-1]}\033[K\n")
        else:
            output.append(f"{part}\033[K")
    return "".join(output)


def _highlight(value: str) -> str:
    return f"\033[7m{value}\033[0m"


def _muted(value: str) -> str:
    return f"\033[90m{value}\033[0m"


def _plain(value: str) -> str:
    return value.replace("\033[7m", "").replace("\033[0m", "").replace("\033[90m", "")


def _badge(value: str) -> str:
    return f"[{value}]"


def _short_path(value: str | None) -> str:
    if not value:
        return "not set"
    path = Path(value)
    if len(str(path)) <= 90:
        return str(path)
    return f"...\\{path.name}"


def compact_menu_line(value: str, width: int | None = None) -> str:
    return clip_text(value, width or _width())


def _pause(prompt: str = "Press Enter to go back...") -> None:
    input(prompt)


def _pause_with_redraw(draw_screen: Callable[[], None], prompt: str = "Press Enter to go back...") -> None:
    if not _interactive_menu_enabled():
        draw_screen()
        _pause(prompt)
        return

    def draw_prompt() -> None:
        draw_screen()
        print(_muted(compact_menu_line(prompt, _width())))

    while True:
        key = _read_key_with_redraw(draw_prompt)
        if key in {"enter", "escape", "q", "b"}:
            return


def _ask_float(label: str, current: float) -> float:
    raw = input(f"{label} [{current:g}]: ").strip().replace(",", "")
    if not raw:
        return current
    try:
        return float(raw)
    except ValueError:
        print("Invalid number; keeping previous value.")
        return current


def _ask_optional_float(label: str, current: float | None) -> float | None:
    current_text = "none" if current is None else f"{current:g}"
    raw = input(f"{label} [{current_text}] (empty keeps, none clears): ").strip().replace(",", "")
    if not raw:
        return current
    if raw.lower() in {"none", "clear", "off", "no"}:
        return None
    try:
        return float(raw)
    except ValueError:
        print("Invalid number; keeping previous value.")
        return current


def _ensure_talisman_attrs(args: argparse.Namespace) -> None:
    defaults = {
        "max_accessory_price": None,
        "max_accessory_recommendations": 15,
        "max_accessory_ah_checks": 60,
        "include_locked_accessories": False,
        "include_uncertain_accessories": True,
        "include_manual_unlocks": True,
        "include_ah_accessories": True,
        "include_craftable_accessories": True,
        "accessory_sort": "score",
        "accessory_rarity": "",
        "accessory_view": "recommended",
        "accessory_search": None,
        "accessories_file": "data/accessories.json",
        "show_locked": False,
    }
    for key, value in defaults.items():
        if not hasattr(args, key):
            setattr(args, key, value)


def _ask_int(label: str, current: int) -> int:
    raw = input(f"{label} [{current}]: ").strip().replace(",", "")
    if not raw:
        return current
    try:
        return int(raw)
    except ValueError:
        print("Invalid integer; keeping previous value.")
        return current


def _coins(value: float | int | None) -> str:
    if value is None:
        return "not set"
    if not isinstance(value, (int, float)):
        return str(value)
    return f"{float(value):,.0f}"


def _value(value: float | int | None, *, coins: bool = False) -> str:
    if coins:
        return _coins(value)
    if value is None:
        return "not set"
    return f"{value:g}"


def _optional_coins(value: float | int | None) -> str:
    return "none" if value is None else _coins(value)
