import pytest

from models.dig import DigitiserModel
from sdr.sdr import SDR, _normalise_sdr_type
from util.xbase import XSoftwareFailure


def test_digitiser_model_defaults_to_rtlsdr_backend():
    dig = DigitiserModel(dig_id="dig001")

    assert dig.sdr_type == "rtlsdr"
    assert dig.sdr_config == {}


def test_digitiser_model_loads_legacy_dict_without_sdr_fields():
    dig = DigitiserModel.from_dict(
        {
            "_type": "DigitiserModel",
            "dig_id": "dig001",
            "load_active": False,
            "gain": 0.0,
            "sample_rate": 0.0,
            "bandwidth": 0.0,
            "center_freq": 0.0,
            "freq_correction": 0,
            "channels": 0,
            "scan_duration": 0,
            "scanning": False,
            "sdr_eeprom": {},
            "last_update": {"_type": "datetime", "value": "2026-01-01T00:00:00+00:00"},
        }
    )

    assert dig.sdr_type == "rtlsdr"
    assert dig.sdr_config == {}


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("rtlsdr", "rtlsdr"),
        ("RTL-SDR", "rtlsdr"),
        ("rtl_sdr", "rtlsdr"),
        ("soapy", "soapy"),
        ("SoapySDR", "soapy"),
        ("airspy", "soapy"),
    ],
)
def test_sdr_type_aliases(configured, expected):
    assert _normalise_sdr_type(configured) == expected


def test_unknown_sdr_type_is_rejected_before_driver_import():
    with pytest.raises(XSoftwareFailure):
        SDR(sdr_type="not-a-real-backend")
