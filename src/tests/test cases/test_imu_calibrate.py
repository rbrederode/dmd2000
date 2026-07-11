from imu.calibrate import calibrate_imu


class FakeIMU:
    connected = True
    alt_vector = "pitch"
    az_vector = "yaw"

    def __init__(self):
        self.imu_data = object()
        self.prompts = []
        self.saved_alt_offset = None
        self.saved_az_offset = None

    @property
    def alt_offset(self):
        return self.saved_alt_offset

    @alt_offset.setter
    def alt_offset(self, value):
        self.saved_alt_offset = value

    @property
    def az_offset(self):
        return self.saved_az_offset

    @az_offset.setter
    def az_offset(self, value):
        self.saved_az_offset = value % 360.0

    def _angle_for_vector(self, _imu_data, vector_name, _valid_vectors):
        return {"pitch": -2.5, "yaw": -64.0}[vector_name]


class FakeFigure:
    def suptitle(self, _title):
        pass


class FakePyplot:
    def subplots(self, *_args, **_kwargs):
        return FakeFigure(), ["raw", "pointing"]


def test_calibrate_imu_uses_single_horizontal_true_north_pointing(monkeypatch):
    fake_imu = FakeIMU()
    prompts = []

    def fake_wait(_imu, _fig, _axes, _plt, message):
        prompts.append(message)

    monkeypatch.setattr("imu.calibrate.plt", FakePyplot())
    monkeypatch.setattr("imu.calibrate._wait_for_calibration_keypress", fake_wait)
    monkeypatch.setattr("imu.calibrate.save_imu_device_list", lambda *_args, **_kwargs: None)

    calibrate_imu(fake_imu, profile="default")

    assert prompts == ["Please point the IMU device horizontally at True North, press a key when ready"]
    assert fake_imu.alt_offset == 2.5
    assert fake_imu.az_offset == 296.0
