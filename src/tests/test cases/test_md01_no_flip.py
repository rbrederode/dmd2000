from types import SimpleNamespace

import pytest

from dsh.drivers.md01.md01_driver import MD01Driver


@pytest.mark.parametrize("operation", ["_slew", "_track"])
def test_md01_commands_requested_coordinates_without_flip(operation):
    driver = MD01Driver.__new__(MD01Driver)
    driver.md01_config = SimpleNamespace(
        host="md01",
        port=23,
        min_alt=0.0,
        max_alt=90.0,
        short_way=False,
    )
    driver.dsh_model = SimpleNamespace(
        desired_altaz={"alt": 90.0, "az": 172.0},
        last_update=None,
    )
    commanded = []
    driver.can_reach = lambda alt, az: 0.0 <= alt <= 90.0
    driver.do_flip = lambda *_args, **_kwargs: pytest.fail("flip logic was called")
    driver._set_md01_altaz = lambda alt, az: commanded.append((alt, az))

    getattr(driver, operation)(90.0, 172.0)

    assert commanded == [(90.0, 172.0)]


@pytest.mark.parametrize("operation", ["_slew", "_track"])
def test_md01_short_way_can_command_flipped_coordinates(operation):
    driver = MD01Driver.__new__(MD01Driver)
    driver.md01_config = SimpleNamespace(
        host="md01",
        port=23,
        min_alt=0.0,
        max_alt=90.0,
        short_way=True,
    )
    driver.dsh_model = SimpleNamespace(
        desired_altaz={"alt": 90.0, "az": 180.0},
        last_update=None,
    )
    commanded = []
    driver.can_reach = lambda alt, az: 0.0 <= alt <= 90.0
    driver.do_flip = lambda *_args, **_kwargs: True
    driver._set_md01_altaz = lambda alt, az: commanded.append((alt, az))

    getattr(driver, operation)(90.0, 180.0)

    assert commanded == [(90.0, 0.0)]
    assert driver.dsh_model.desired_altaz == {"alt": 90.0, "az": 0.0}


def test_md01_short_way_compares_literal_encoder_distances():
    driver = MD01Driver.__new__(MD01Driver)
    driver.md01_config = SimpleNamespace(short_way=True)
    driver.can_reach = lambda _alt, _az: True
    driver._get_md01_altaz = lambda: (20.0, 350.0)

    assert driver.do_flip(80.0, 10.0) is True
