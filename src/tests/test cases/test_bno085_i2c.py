import sys
import types

import pytest

from imu.drivers.bno085 import BNO085Config, BNO085Driver
from models.imu import IMUDeviceModel, IMUDriverType


def test_bno085_config_is_i2c_only():
    config = BNO085Config(i2c_bus=1, address=0x4A, refresh_rate=20)

    assert config.i2c_bus == 1
    assert config.address == 0x4A
    assert "device" not in config._data
    assert "baudrate" not in config._data

    with pytest.raises(ValueError):
        BNO085Config(device="/dev/serial0", baudrate=115200)


def test_bno085_numbered_i2c_bus_uses_extended_bus(monkeypatch):
    created = []

    class FakeExtendedI2C:
        def __init__(self, bus_number):
            self.bus_number = bus_number
            created.append(bus_number)

    fake_module = types.ModuleType("adafruit_extended_bus")
    fake_module.ExtendedI2C = FakeExtendedI2C
    monkeypatch.setitem(sys.modules, "adafruit_extended_bus", fake_module)

    config = BNO085Config(i2c_bus=3, address=0x4B)
    imu_model = IMUDeviceModel(
        imu_id="imu001",
        driver_type=IMUDriverType.BNO085,
        driver_config=config,
    )
    driver = BNO085Driver(imu_model=imu_model)

    i2c = driver._make_i2c_bus()

    assert isinstance(i2c, FakeExtendedI2C)
    assert i2c.bus_number == 3
    assert created == [3]


def test_bno085_enable_features_uses_configured_report_interval(monkeypatch):
    enabled_features = []

    class FakeBNO08XI2C:
        def __init__(self, i2c, address):
            self.i2c = i2c
            self.address = address

        def enable_feature(self, feature, report_interval=None):
            enabled_features.append((feature, report_interval))

    fake_bno08x = types.ModuleType("adafruit_bno08x")
    fake_bno08x.BNO_REPORT_ACCELEROMETER = 1
    fake_bno08x.BNO_REPORT_GYROSCOPE = 2
    fake_bno08x.BNO_REPORT_MAGNETOMETER = 3
    fake_bno08x.BNO_REPORT_ROTATION_VECTOR = 5
    fake_bno08x_i2c = types.ModuleType("adafruit_bno08x.i2c")
    fake_bno08x_i2c.BNO08X_I2C = FakeBNO08XI2C
    monkeypatch.setitem(sys.modules, "adafruit_bno08x", fake_bno08x)
    monkeypatch.setitem(sys.modules, "adafruit_bno08x.i2c", fake_bno08x_i2c)

    config = BNO085Config(i2c_bus=3, address=0x4A, refresh_rate=1)
    imu_model = IMUDeviceModel(
        imu_id="imu001",
        driver_type=IMUDriverType.BNO085,
        driver_config=config,
    )
    driver = BNO085Driver(imu_model=imu_model)
    driver._make_i2c_bus = lambda: object()
    driver._poll_loop = lambda: None

    assert driver._connect() is True

    assert enabled_features == [
        (1, 1_000_000),
        (2, 1_000_000),
        (3, 1_000_000),
        (5, 1_000_000),
    ]
