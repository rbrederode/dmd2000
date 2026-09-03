from __future__ import annotations

from models.comms import CommunicationStatus
from util.xbase import XSoftwareFailure

import logging

logger = logging.getLogger(__name__)


SDR_TYPE_RTLSDR = "rtlsdr"
SDR_TYPE_SOAPY = "soapy"
SDR_TYPE_AIRSPY = "airspy"
SDR_TYPE_GQRXRAW = "gqrxraw"
SDR_TYPE_AIRSTREAM = "airstream"
SDR_TYPE_RTLSTREAM = "rtlstream"
DEFAULT_READ_SIZE = 256 * 1024


class SDR:
    """Public SDR interface that delegates hardware access to a selected driver."""

    def __init__(self, bias_t_enabled: bool = False, sdr_type: str | None = None, sdr_config: dict | None = None):
        self.sdr_type = _normalise_sdr_type(sdr_type)
        self.sdr_config = sdr_config or {}
        self.driver = self._create_driver(bias_t_enabled=bias_t_enabled)

    def _create_driver(self, bias_t_enabled: bool):
        if self.sdr_type == SDR_TYPE_RTLSDR:
            from sdr.drivers.rtlsdr import SDR as RTLSDRDriver

            return RTLSDRDriver(bias_t_enabled=bias_t_enabled, sdr_config=self.sdr_config)

        if self.sdr_type in {SDR_TYPE_SOAPY, SDR_TYPE_AIRSPY}:
            from sdr.drivers.soapy import SDR as SoapySDRDriver

            return SoapySDRDriver(bias_t_enabled=bias_t_enabled, sdr_config=self.sdr_config)

        if self.sdr_type == SDR_TYPE_GQRXRAW:
            from sdr.drivers.gqrx import SDR as GQRXReplayDriver

            return GQRXReplayDriver(bias_t_enabled=bias_t_enabled, sdr_config=self.sdr_config)

        if self.sdr_type in {SDR_TYPE_AIRSTREAM, SDR_TYPE_RTLSTREAM}:
            from sdr.drivers.stream import SDR as StreamSDRDriver

            sdr_config = dict(self.sdr_config)
            sdr_config.setdefault("stream_backend", _stream_backend_for_sdr_type(self.sdr_type))
            return StreamSDRDriver(bias_t_enabled=bias_t_enabled, sdr_config=sdr_config)

        raise XSoftwareFailure(f"Unsupported SDR type: {self.sdr_type}")

    def get_comms_status(self) -> CommunicationStatus:
        return self.driver.get_comms_status()

    def close(self):
        return self.driver.close()

    def stream_reset(self) -> int:
        """Reset a streaming backend; non-streaming backends have no buffer."""
        reset = getattr(self.driver, "stream_reset", None)
        return reset() if callable(reset) else 0

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        try:
            return getattr(self.driver, name)
        except AttributeError as exc:
            raise AttributeError(f"{type(self).__name__!s} object has no attribute {name!r}") from exc


def _normalise_sdr_type(sdr_type: str | None) -> str:
    if sdr_type is None or str(sdr_type).strip() == "":
        return SDR_TYPE_RTLSDR

    value = str(sdr_type).strip().lower()
    aliases = {
        "rtlsdr": SDR_TYPE_RTLSDR,
        "soapy": SDR_TYPE_SOAPY,
        "airspy": SDR_TYPE_AIRSPY,
        "gqrxraw": SDR_TYPE_GQRXRAW,
        "airstream": SDR_TYPE_AIRSTREAM,
        "rtlstream": SDR_TYPE_RTLSTREAM,
    }

    if value not in aliases:
        raise XSoftwareFailure(f"Unsupported SDR type: {sdr_type}")

    return aliases[value]


def _stream_backend_for_sdr_type(sdr_type: str) -> str:
    if sdr_type == SDR_TYPE_RTLSTREAM:
        return "rtl"
    return "airspy"
