import pytest

from dsh.drivers.driver import DishDriver
from models.dsh import DishMode, DishModel, PointingState


def make_ready_driver(pointing_altaz):
    model = DishModel(
        dsh_id="dish-test",
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.READY,
        pointing_altaz=pointing_altaz,
    )
    return DishDriver(dsh_model=model)


def test_equivalent_wrapped_azimuth_is_stationary():
    driver = make_ready_driver({"alt": 87.2, "az": 359.9})
    driver._get_current_altaz = lambda: (87.2, -0.1)

    driver.get_current_altaz()

    assert driver.dsh_model.pointing_altaz["az"] == pytest.approx(359.9)
    assert driver.dsh_model.velocity_altaz == pytest.approx({"alt": 0.0, "az": 0.0})
    assert driver.dsh_model.last_err_msg is None


def test_azimuth_velocity_uses_shortest_distance_across_north():
    driver = make_ready_driver({"alt": 45.0, "az": 359.9})
    driver._get_current_altaz = lambda: (45.0, 0.1)

    driver.get_current_altaz()

    assert driver.dsh_model.velocity_altaz["az"] == pytest.approx(0.2)


def test_repeated_movement_errors_do_not_embed_previous_error():
    driver = make_ready_driver({"alt": 45.0, "az": 10.0})
    driver.dsh_model.set_last_err("sentinel previous error")
    readings = iter([(45.0, 11.0), (45.0, 12.0)])
    driver._get_current_altaz = lambda: next(readings)

    driver.get_current_altaz()
    first_error = driver.dsh_model.last_err_msg
    driver.get_current_altaz()
    second_error = driver.dsh_model.last_err_msg

    assert "sentinel previous error" not in first_error
    assert "last_err_msg" not in first_error
    assert first_error not in second_error
    assert "last_err_msg" not in second_error
    assert len(second_error) < len(first_error) + 100
