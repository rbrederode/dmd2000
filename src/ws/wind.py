import os
import platform
import time
from dataclasses import dataclass


"""
Read a 4-20 mA anemometer via a current-to-voltage converter.

The expected signal chain is:

    anemometer current output -> current-to-voltage converter -> ADC -> Raspberry Pi

Typical converter setup for a Pi:
    4-20 mA input scaled to 0-3.3 V output

Typical ADC:
    ADS1115 on I2C

Environment variables:
    WIND_ADC_BACKEND       ads1115 | mock
    WIND_ADC_CHANNEL       0-3, default 0
    WIND_ADC_ADDRESS       I2C address, default 0x48
    WIND_VOLTAGE_MIN       converter output at minimum wind speed, default 0.0
    WIND_VOLTAGE_MAX       converter output at maximum wind speed, default 3.3
    WIND_SPEED_MIN         minimum wind speed in m/s, default 0.0
    WIND_SPEED_MAX         maximum wind speed in m/s, default 30.0
    WIND_POLL_INTERVAL     seconds, default 1.0
    WIND_TEST_VOLTAGE      used by mock backend, default 1.65

Examples: 
    python ws/wind.py
    WIND_ADC_BACKEND=mock WIND_TEST_VOLTAGE=2.2 python ws/wind.py
"""


@dataclass
class WindConfig:
    adc_backend: str = os.environ.get("WIND_ADC_BACKEND", "ads1115")
    adc_channel: int = int(os.environ.get("WIND_ADC_CHANNEL", "0"))
    adc_address: int = int(os.environ.get("WIND_ADC_ADDRESS", "0x48"), 0)
    voltage_min: float = float(os.environ.get("WIND_VOLTAGE_MIN", "0.0"))
    voltage_max: float = float(os.environ.get("WIND_VOLTAGE_MAX", "3.3"))
    speed_min: float = float(os.environ.get("WIND_SPEED_MIN", "0.0"))
    speed_max: float = float(os.environ.get("WIND_SPEED_MAX", "30.0"))
    poll_interval: float = float(os.environ.get("WIND_POLL_INTERVAL", "1.0"))


class VoltageReader:
    """Abstract voltage reader interface."""

    def read_voltage(self) -> float:
        raise NotImplementedError


class MockVoltageReader(VoltageReader):
    """Simple test reader for development off-target."""

    def __init__(self):
        self.voltage = float(os.environ.get("WIND_TEST_VOLTAGE", "1.65"))

    def read_voltage(self) -> float:
        return self.voltage


class ADS1115VoltageReader(VoltageReader):
    """Read converter output voltage from an ADS1115 ADC."""

    def __init__(self, channel: int = 0, address: int = 0x48):
        try:
            import board
            import busio
            import adafruit_ads1x15.ads1115 as ADS
            from adafruit_ads1x15.ads1x15 import Pin
            from adafruit_ads1x15.analog_in import AnalogIn
        except ImportError as exc:
            raise ImportError(
                "ADS1115 backend requires board, busio, and adafruit_ads1x15. "
                "Install CircuitPython ADS1x15 packages on the Raspberry Pi."
            ) from exc

        if channel not in (0, 1, 2, 3):
            raise ValueError(f"ADS1115 channel must be 0-3, got {channel}")

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c, address=address)
        ads.gain = 1  # +-4.096 V, suitable for a 0-3.3 V converter output

        channel_map = {
            0: getattr(ADS, "P0", Pin.A0),
            1: getattr(ADS, "P1", Pin.A1),
            2: getattr(ADS, "P2", Pin.A2),
            3: getattr(ADS, "P3", Pin.A3),
        }
        self.chan = AnalogIn(ads, channel_map[channel])

    def read_voltage(self) -> float:
        return float(self.chan.voltage)


def build_voltage_reader(config: WindConfig) -> VoltageReader:
    backend = config.adc_backend.lower()

    if backend == "mock":
        return MockVoltageReader()

    if backend == "ads1115":
        return ADS1115VoltageReader(channel=config.adc_channel, address=config.adc_address)

    raise ValueError(f"Unsupported WIND_ADC_BACKEND '{config.adc_backend}'")


def voltage_to_wind_speed(voltage: float, config: WindConfig) -> float:
    """Convert ADC voltage to wind speed using calibrated converter endpoints."""
    if config.voltage_max <= config.voltage_min:
        raise ValueError(
            f"WIND_VOLTAGE_MAX must be greater than WIND_VOLTAGE_MIN, got "
            f"{config.voltage_max} <= {config.voltage_min}"
        )

    clamped_voltage = min(max(voltage, config.voltage_min), config.voltage_max)
    fraction = (clamped_voltage - config.voltage_min) / (config.voltage_max - config.voltage_min)
    return config.speed_min + fraction * (config.speed_max - config.speed_min)


def describe_environment(config: WindConfig) -> str:
    return (
        f"platform={platform.system()} "
        f"backend={config.adc_backend} "
        f"channel={config.adc_channel} "
        f"address={hex(config.adc_address)} "
        f"voltage_range={config.voltage_min:.3f}-{config.voltage_max:.3f}V "
        f"speed_range={config.speed_min:.3f}-{config.speed_max:.3f}m/s"
    )


def main():
    config = WindConfig()
    reader = build_voltage_reader(config)

    print("Wind reader starting with", describe_environment(config))
    print("Expecting a current-to-voltage converter, not direct pulse or RS485 input.")

    while True:
        try:
            voltage = reader.read_voltage()
            wind_speed = voltage_to_wind_speed(voltage, config)
            print(f"Voltage: {voltage:.3f} V | Wind speed: {wind_speed:.2f} m/s")
        except Exception as exc:
            print("Read error:", exc)

        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
