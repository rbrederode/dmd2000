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
    )
    commanded = []
    driver.can_reach = lambda alt, az: 0.0 <= alt <= 90.0
    driver.do_flip = lambda *_args, **_kwargs: pytest.fail("flip logic was called")
    driver._set_md01_altaz = lambda alt, az: commanded.append((alt, az))

    getattr(driver, operation)(90.0, 172.0)

    assert commanded == [(90.0, 172.0)]
