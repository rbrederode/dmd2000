from queue import Queue

from dig.dig import Digitiser
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from models.health import HealthState
from models.temp import TempReading


class FakeTempSensor:
    def get_comms_status(self):
        return CommunicationStatus.ESTABLISHED

    def stop(self):
        pass


def test_digitiser_health_is_ok_when_temperature_sensor_is_healthy():
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.dig_model = DigitiserModel(
        sdr_connected=CommunicationStatus.ESTABLISHED,
        tm_connected=CommunicationStatus.ESTABLISHED,
        sdp_connected=CommunicationStatus.ESTABLISHED,
        temp_reading=TempReading(temperature=20.0),
        temp_max=40.0,
    )
    digitiser.app_model = digitiser.dig_model.app
    digitiser.processors = []
    digitiser.status_thread = None
    digitiser.queue = Queue()
    digitiser.temp_sensor = FakeTempSensor()

    assert digitiser.get_health_state() == HealthState.OK
