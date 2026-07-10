from datetime import datetime, timezone
from api.imu_transport import (
    IMUTCPClient,
    build_imu_data_request,
    decode_api_message,
    imu_data_from_response,
)
from api.imu_api import IMU_API
from env.events import DataEvent
from models.imu import IMUData


class FakeIMU:
    def __init__(self, imu_data):
        self.imu_data = imu_data

    def get_imu_data(self):
        return self.imu_data.copy()


class CaptureEndpoint:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


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

    request = build_imu_data_request(entity="dsh001", request_id="req-001")
    request_bytes = request.to_data()

    client = IMUTCPClient.__new__(IMUTCPClient)
    client.api = IMU_API()
    client.imu = FakeIMU(sample)
    capture = CaptureEndpoint()
    client.endpoint = capture

    event = DataEvent(
        local_sap=None,
        remote_conn=None,
        remote_addr=("127.0.0.1", 50010),
        data=request_bytes,
        timestamp=datetime.now(timezone.utc),
    )
    client.handle_message_event(event)

    response = capture.sent[0]
    response_bytes = response.to_data()
    decoded_response = decode_api_message(response_bytes)
    decoded_imu_data = imu_data_from_response(decoded_response)

    assert decoded_response.get_from_system() == "client"
    assert decoded_response.get_to_system() == "server"
    assert decoded_response.get_entity() == "dsh001"
    assert decoded_response.get_echo_data() == {"request_id": "req-001"}
    assert decoded_imu_data.imu_id == sample.imu_id
    assert decoded_imu_data.angle == sample.angle
    assert decoded_imu_data.temp_celsius == sample.temp_celsius
