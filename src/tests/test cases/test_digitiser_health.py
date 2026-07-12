from queue import Queue
import threading

import dig.dig as dig_module
from dig.dig import Digitiser
from dig.filters.sawbird import SAWbirdH1_BareBones
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


def test_digitiser_reuses_existing_bpf_power_relay(monkeypatch):
    created_pins = []

    class FakeLED:
        def __init__(self, pin):
            self.pin = pin
            self.is_active = False
            created_pins.append(pin)

        def on(self):
            self.is_active = True

        def off(self):
            self.is_active = False

        def close(self):
            pass

    digitiser = Digitiser.__new__(Digitiser)
    digitiser.dig_model = DigitiserModel(
        bpf_config=SAWbirdH1_BareBones(gpio_pin_power=17, gpio_pin_load=18),
    )
    digitiser.app_model = digitiser.dig_model.app
    digitiser.processors = []
    digitiser.status_thread = None
    digitiser.queue = Queue()
    digitiser.temp_sensor = None
    digitiser.load_relay = None
    digitiser.power_relay = None
    digitiser._bpf_control_state = {"load": None, "power": None}
    digitiser._bpf_control_pin = {"load": None, "power": None}
    digitiser._bpf_control_lock = threading.Lock()

    monkeypatch.setattr(dig_module, "LED", FakeLED)

    digitiser.set_bpf_power_state(True)
    digitiser.set_bpf_power_state(True)

    assert created_pins == [17]
    assert digitiser.power_relay.is_active is True
