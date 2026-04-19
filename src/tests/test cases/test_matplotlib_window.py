from types import SimpleNamespace

from util.matplotlib_window import get_figure_visibility


class FakeQtWindow:
    def __init__(self, visible):
        self._visible = visible

    def isVisible(self):
        return self._visible


class FakeTkWindow:
    def __init__(self, state):
        self._state = state

    def state(self):
        return self._state


def make_figure(window):
    manager = SimpleNamespace(window=window)
    canvas = SimpleNamespace(manager=manager)
    return SimpleNamespace(canvas=canvas)


def test_get_figure_visibility_uses_backend_window_visibility():
    fig = make_figure(FakeQtWindow(visible=True))

    assert get_figure_visibility(fig) is True


def test_get_figure_visibility_detects_hidden_tk_window():
    fig = make_figure(FakeTkWindow(state="withdrawn"))

    assert get_figure_visibility(fig) is False


def test_get_figure_visibility_returns_none_without_window():
    fig = make_figure(window=None)

    assert get_figure_visibility(fig) is None
