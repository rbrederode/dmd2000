from datetime import datetime, timezone
from api.imu_app import IMU_APP, PROPERTY_IMU_DATA
from imu.imu import IMUProvider
from imu.server import IMUServer
from sdk.ipc.message import APIMessage
from sdk.models.imu import IMUData


class FakeIMU(IMUProvider):
    def __init__(self, imu_data):
        self.imu_data = imu_data

    def get_imu_data(self):
        return self.imu_data.copy()


class CaptureSocket:
    def __init__(self, request):
        self.request = request
        self.sent = []
        self.closed = False

    def recv(self, size):
        return self.request

    def send(self, msg):
        self.sent.append(msg)
        return len(msg)

    def close(self):
        self.closed = True


def test_imu_request_response_roundtrip_preserves_imu_data():
    sample = IMUData(
        imu_id="imu001",
        acceleration=[1.0, 2.0, 3.0],
        angle=[10.0, 20.0, 30.0],
        angular_vel=[0.1, 0.2, 0.3],
        magnetic_vector=[4.0, 5.0, 6.0],
        temp_celsius=21.5,
        quaternion=[1.0, 0.0, 0.0, 0.0],
        last_update=datetime.now(timezone.utc),
    )

    api = IMU_APP()
    request = APIMessage()
    request.set_json_api_header(
        api_version=api.get_api_version(),
        dt=datetime.now(timezone.utc),
        from_system="app", to_system="imu", entity="dsh001",
        api_call={"msg_type": "req", "action_code": "get", "property": PROPERTY_IMU_DATA},
        echo={"request_id": "req-001"},
    )
    api.validate(request.get_json_api_header())
    server = IMUServer(FakeIMU(sample))
    capture = CaptureSocket(request.to_data())
    server._handle_client(capture, ("127.0.0.1", 50010))

    assert capture.closed
    assert len(capture.sent) == 1
    decoded_response = APIMessage()
    decoded_response.from_data(capture.sent[0])
    api.validate(decoded_response.get_json_api_header())
    assert decoded_response.get_api_call()["status"] == "success"
    decoded_imu_data = IMUData.from_dict(decoded_response.get_api_call()["value"])

    assert decoded_response.get_from_system() == "imu"
    assert decoded_response.get_to_system() == "app"
    assert decoded_response.get_entity() == "dsh001"
    assert decoded_response.get_echo_data() == {"request_id": "req-001"}
    assert decoded_imu_data.to_dict() == sample.to_dict()
