import pytest

from dsh.drivers.md01.md01_config import MD01Config
from util.xbase import XAPIValidationFailed


def test_md01_config_defaults_include_controller_azimuth_range():
    config = MD01Config()

    assert config.min_az == -180.0
    assert config.max_az == 540.0


def test_md01_config_azimuth_limits_round_trip():
    config = MD01Config(min_az=-10.0, max_az=370.0)

    restored = MD01Config.from_dict(config.to_dict())

    assert restored.min_az == -10.0
    assert restored.max_az == 370.0


@pytest.mark.parametrize(
    ("min_az", "max_az"),
    [
        (-180.1, 360.0),
        (0.0, 540.1),
        (100.0, 99.9),
    ],
)
def test_md01_config_rejects_invalid_azimuth_limits(min_az, max_az):
    with pytest.raises(XAPIValidationFailed):
        MD01Config(min_az=min_az, max_az=max_az)
