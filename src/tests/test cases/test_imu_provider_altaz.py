from datetime import datetime, timezone

from imu.imu import IMU
from models.comms import CommunicationStatus
from models.imu import IMUData, IMUDeviceModel


class FakeDriver:
    def __init__(self, imu_data):
        self.imu_data = imu_data

    def get_imu_data(self):
        return self.imu_data.copy()


def test_get_imu_data_populates_computed_altaz():
    sample = IMUData(
        imu_id="imu002",
        angle=[10.0, 20.0, 30.0],
        last_update=datetime.now(timezone.utc),
    )
    imu_device = IMUDeviceModel(
        imu_id="imu002",
        alt_vector="roll",
        az_vector="yaw",
        alt_offset=5.0,
        az_offset=15.0,
        imu_connected=CommunicationStatus.ESTABLISHED,
    )
    imu = IMU.__new__(IMU)
    imu.imu_device = imu_device
    imu.driver = FakeDriver(sample)

    imu_data = imu.get_imu_data()

    assert imu_data.altaz == (15.0, 345.0)
    assert sample.altaz is None
