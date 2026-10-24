from models.dsh import DishModel
from dsh.drivers.driver import DishDriver
from dsh.drivers.md01.md01_config import MD01Config
from dsh.drivers.md01.md01_driver import MD01Driver


def make_driver(current, desired, rotation_speed=2.5, resolution=0.1):
    dish = DishModel(
        dsh_id="dish001",
        pointing_altaz=current,
        desired_altaz=desired,
    )
    driver = DishDriver(dsh_model=dish)
    driver._get_rotation_speed = lambda: rotation_speed
    driver._get_resolution = lambda: resolution
    return driver


def make_md01_driver(current, desired, short_way, rotation_speed=2.5):
    config = MD01Config(
        rotation_speed=rotation_speed,
        resolution=0.1,
        short_way=short_way,
        min_alt=0.0,
        max_alt=90.0,
    )
    dish = DishModel(
        dsh_id="dish003",
        pointing_altaz=current,
        desired_altaz=desired,
        driver_config=config,
    )
    return MD01Driver(dsh_model=dish)


def test_estimated_slew_duration_uses_slowest_concurrent_axis():
    driver = make_driver(
        current={"alt": 30.0, "az": 100.0},
        desired={"alt": 35.0, "az": 111.0},
    )

    assert driver.estimate_slew_duration() == 5


def test_estimated_slew_duration_uses_short_azimuth_distance():
    driver = make_driver(
        current={"alt": 30.0, "az": 350.0},
        desired={"alt": 30.0, "az": 10.0},
    )

    assert driver.estimate_slew_duration() == 8


def test_estimated_slew_duration_is_zero_when_already_on_target():
    driver = make_driver(
        current={"alt": 30.0, "az": 100.0},
        desired={"alt": 30.05, "az": 100.05},
    )

    assert driver.estimate_slew_duration() == 0


def test_estimated_slew_duration_is_unknown_without_motion_data():
    driver = make_driver(
        current=None,
        desired={"alt": 30.0, "az": 100.0},
    )

    assert driver.estimate_slew_duration() is None


def test_md01_estimate_uses_literal_azimuth_distance_when_short_way_disabled():
    driver = make_md01_driver(
        current={"alt": 52.7, "az": 359.0},
        desired={"alt": 52.7, "az": 0.0},
        short_way=False,
    )

    assert driver.estimate_slew_duration() == 144


def test_md01_estimate_uses_original_distance_when_flip_is_not_selected():
    driver = make_md01_driver(
        current={"alt": 52.7, "az": 359.0},
        desired={"alt": 52.7, "az": 0.0},
        short_way=True,
    )

    assert driver.estimate_slew_duration() == 144


def test_md01_estimate_uses_selected_flipped_coordinates():
    driver = make_md01_driver(
        current={"alt": 90.0, "az": 0.0},
        desired={"alt": 90.0, "az": 180.0},
        short_way=True,
    )
    driver._get_md01_altaz = lambda: (90.0, 0.0)
    driver._set_md01_altaz = lambda _alt, _az: None

    driver._slew(90.0, 180.0)

    assert driver.dsh_model.desired_altaz == {"alt": 90.0, "az": 0.0}
    assert driver.estimate_slew_duration() == 0


def test_md01_estimate_uses_literal_distance_to_selected_flipped_coordinates():
    driver = make_md01_driver(
        current={"alt": 20.0, "az": 350.0},
        desired={"alt": 100.0, "az": 10.0},
        short_way=True,
        rotation_speed=10.0,
    )
    driver._get_md01_altaz = lambda: (20.0, 350.0)
    driver._set_md01_altaz = lambda _alt, _az: None

    driver._slew(100.0, 10.0)

    assert driver.dsh_model.desired_altaz == {"alt": 80.0, "az": 190.0}
    assert driver.estimate_slew_duration() == 16


def test_md01_estimate_uses_slowest_concurrent_axis_without_short_way():
    driver = make_md01_driver(
        current={"alt": 10.0, "az": 350.0},
        desired={"alt": 60.0, "az": 10.0},
        short_way=False,
        rotation_speed=10.0,
    )

    assert driver.estimate_slew_duration() == 34
