import pytest

from imu.imu import IMU
from util.convert import angle_to_altitude


class FakeIMUDevice:
    alt_offset = 0.0
    last_update = None


def test_angle_to_altitude_defaults_missing_offset_to_zero():
    assert angle_to_altitude(42.0, None) == (42.0, False)


def test_angle_to_altitude_reports_invalid_offset_value():
    with pytest.raises(ValueError, match="120.0"):
        angle_to_altitude(0.0, 120.0)


def test_imu_alt_offset_setter_rejects_invalid_offset():
    imu = IMU.__new__(IMU)
    imu.imu_device = FakeIMUDevice()

    with pytest.raises(ValueError, match="120.0"):
        imu.alt_offset = 120.0

    assert imu.imu_device.alt_offset == 0.0
