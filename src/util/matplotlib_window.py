from __future__ import annotations

from typing import Any, Optional

try:
    from AppKit import NSApplication

    HAS_APPKIT = True
except ImportError:
    NSApplication = None
    HAS_APPKIT = False


def get_figure_visibility(fig: Any) -> Optional[bool]:
    """Best-effort figure visibility check across matplotlib GUI backends.

    Returns:
        True when the figure is visible, False when it is known to be hidden,
        or None when the backend cannot report visibility.
    """
    if fig is None:
        return None

    canvas = getattr(fig, "canvas", None)
    manager = getattr(canvas, "manager", None)
    if manager is None:
        return None

    macos_visibility = _get_macos_key_window_visibility(manager)
    if macos_visibility is not None:
        return macos_visibility

    return _get_backend_window_visibility(getattr(manager, "window", None))


def _get_macos_key_window_visibility(manager: Any) -> Optional[bool]:
    """Use AppKit on macOS to preserve the existing active-window behaviour."""
    if not HAS_APPKIT:
        return None

    try:
        key_window = NSApplication.sharedApplication().keyWindow()
    except Exception:
        return None

    if key_window is None:
        return None

    get_window_title = getattr(manager, "get_window_title", None)
    if not callable(get_window_title):
        return None

    try:
        fig_title = get_window_title()
        key_window_title = key_window.title()
    except Exception:
        return None

    if fig_title is None or key_window_title is None:
        return None

    return fig_title == key_window_title


def _get_backend_window_visibility(window: Any) -> Optional[bool]:
    """Query common GUI window APIs exposed by matplotlib backend managers."""
    if window is None:
        return None

    for attr_name in ("isVisible", "get_visible", "IsShownOnScreen", "IsShown"):
        visible = _call_window_method(window, attr_name)
        if visible is not None:
            return visible

    tk_viewable = _call_window_method(window, "winfo_viewable")
    if tk_viewable is not None:
        return tk_viewable

    tk_state = _call_window_method(window, "state", convert_to_bool=False)
    if isinstance(tk_state, str):
        return tk_state not in {"iconic", "withdrawn"}

    return None


def _call_window_method(window: Any, attr_name: str, convert_to_bool: bool = True) -> Any:
    method = getattr(window, attr_name, None)
    if not callable(method):
        return None

    try:
        value = method()
    except Exception:
        return None

    if convert_to_bool:
        return bool(value)

    return value
