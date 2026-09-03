from types import SimpleNamespace
from unittest import mock

from dsh.dm import DM
from dsh.dish_display import DishDisplay
from dsh.weather_display import WeatherDisplay


class FakeDisplay:
    def __init__(self, driver=None, weather_store=None):
        self.driver = driver
        self.weather_store = weather_store
        self.active = True
        self.closed = False

    def set_is_active(self, active):
        self.active = active

    def get_is_active(self):
        return self.active

    def _close_figure(self):
        self.closed = True


def make_manager():
    manager = DM.__new__(DM)
    manager.stop = lambda: None
    manager.dish_displays = {}
    manager.weather_displays = {}
    return manager


def test_stale_dish_display_is_closed_and_recreated_for_new_driver():
    manager = make_manager()
    old_driver = object()
    new_driver = object()
    old_display = FakeDisplay(driver=old_driver)
    manager.dish_displays["dish001"] = old_display
    manager._create_dish_display = lambda dish_driver: FakeDisplay(driver=dish_driver)

    display = manager._get_dish_display("dish001", new_driver)

    assert old_display.active is False
    assert old_display.closed is True
    assert display is manager.dish_displays["dish001"]
    assert display.driver is new_driver


def test_current_dish_display_is_reused():
    manager = make_manager()
    driver = object()
    existing = FakeDisplay(driver=driver)
    manager.dish_displays["dish001"] = existing

    display = manager._get_dish_display("dish001", driver)

    assert display is existing
    assert existing.active is True
    assert existing.closed is False


def test_stale_weather_display_is_closed_and_recreated_for_new_store():
    manager = make_manager()
    old_store = object()
    new_store = object()
    old_display = FakeDisplay(weather_store=old_store)
    manager.weather_displays["ws001"] = old_display
    manager.dm_model = SimpleNamespace(weather_store=new_store)
    manager._create_weather_display = lambda ws_id: FakeDisplay(
        weather_store=manager.dm_model.weather_store
    )

    display = manager._get_weather_display("ws001")

    assert old_display.active is False
    assert old_display.closed is True
    assert display is manager.weather_displays["ws001"]
    assert display.weather_store is new_store


def test_current_weather_display_is_reused():
    manager = make_manager()
    weather_store = object()
    existing = FakeDisplay(weather_store=weather_store)
    manager.weather_displays["ws001"] = existing
    manager.dm_model = SimpleNamespace(weather_store=weather_store)

    display = manager._get_weather_display("ws001")

    assert display is existing
    assert existing.active is True
    assert existing.closed is False


def test_recreated_dish_display_clears_reused_named_figure():
    display = DishDisplay.__new__(DishDisplay)
    display.driver = SimpleNamespace(
        dsh_model=SimpleNamespace(
            dsh_id="dish001",
            diameter=1.0,
            fd_ratio=0.4,
        )
    )
    display.gs0 = [object(), object(), object()]
    display.gs1 = [object(), object()]
    figure = mock.MagicMock()

    with mock.patch("dsh.dish_display.plt.figure", return_value=figure) as create_figure, \
            mock.patch.object(display, "init_attribute_axes"), \
            mock.patch.object(display, "init_pointing_axes"), \
            mock.patch.object(display, "init_desired_axes"), \
            mock.patch.object(display, "init_mode_axis"), \
            mock.patch.object(display, "init_pec_axes"), \
            mock.patch.object(display, "_create_timeline_artists"):
        display._create_figure()

    assert create_figure.call_args.kwargs["clear"] is True


def test_recreated_weather_display_clears_reused_named_figure():
    display = WeatherDisplay.__new__(WeatherDisplay)
    display.ws = SimpleNamespace(
        ws_id="ws001",
        latitude=53.0,
        longitude=-2.0,
    )
    display.gs = [object(), object()]
    figure = mock.MagicMock()

    with mock.patch("dsh.weather_display.plt.figure", return_value=figure) as create_figure, \
            mock.patch("dsh.weather_display.GridSpecFromSubplotSpec", return_value=[object(), object()]), \
            mock.patch.object(display, "_init_attribute_axes"), \
            mock.patch.object(display, "_init_plot_axes"):
        display._create_figure()

    assert create_figure.call_args.kwargs["clear"] is True
