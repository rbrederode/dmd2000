from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel

class FilterBank(BaseModel):
    """ A class representing the parameters required to create a filterbank file.
        Filterbank files contain the raw power spectra for a scan and are used for post-processing and analysis.
        They can be processed by tools such as PRESTO, SIGPROC, etc. for pulsar searching and other analyses.
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "FilterBank"),
        "enabled": And(bool, lambda v: isinstance(v, bool)), # Whether filterbank file creation is enabled
        "sub_bandwidth": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and v >= 0.0)), # Output sub-bandwidth in Hz for the generated filterbank product. None or 0 means the full scan bandwidth is used.
        "sub_center_freq": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and v >= 0.0)), # Output sub-band center frequency in Hz. None uses the scan center frequency
        "temporal_resolution": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and 1.0 <= v <= 1000.0)),
        "gap_mean_duration": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and 0.0 <= v <= 1000.0)), # Duration either side of a data gap to average when filling gaps (milliseconds)
        "dtype": And(str, lambda v: v in ["uint8", "uint16", "float32", "float64"]), # Data type for filterbank output (e.g. uint16)
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "FilterBank",
            "enabled": False,                       # Whether filterbank file creation is enabled
            "sub_bandwidth": None,                  # Output sub-bandwidth in Hz for the generated filterbank product. None or 0 means the full scan bandwidth is used.
            "sub_center_freq": None,                # Output sub-band center frequency in Hz. None uses the scan center frequency
            "temporal_resolution": None,            # Time resolution (milliseconds) for summing power spectra (e.g. 1 millisecond)
            "gap_mean_duration": 1.0,               # Duration either side of a data gap to average when filling gaps (milliseconds)
            "dtype": "uint8",                       # Data type for filterbank output (e.g. uint16)
         }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def resolve_subband(self, scan_center_freq: float, scan_bandwidth: float) -> tuple[float, float]:
        """Return and validate the effective filterbank subband.

        ``sub_center_freq=None`` means the subband is centered on the scan.
        ``sub_bandwidth=None`` or ``0`` means the full scan bandwidth is used.
        """
        scan_center_freq = float(scan_center_freq)
        scan_bandwidth = float(scan_bandwidth)
        if scan_bandwidth <= 0.0:
            raise ValueError(f"FilterBank requires a positive scan bandwidth; got {scan_bandwidth:.1f} Hz.")

        sub_center_freq = scan_center_freq if self.sub_center_freq is None else float(self.sub_center_freq)
        sub_bandwidth = scan_bandwidth if self.sub_bandwidth is None or float(self.sub_bandwidth) <= 0.0 else float(self.sub_bandwidth)

        scan_lower = scan_center_freq - scan_bandwidth / 2.0
        scan_upper = scan_center_freq + scan_bandwidth / 2.0
        sub_lower = sub_center_freq - sub_bandwidth / 2.0
        sub_upper = sub_center_freq + sub_bandwidth / 2.0

        if sub_bandwidth > scan_bandwidth or sub_lower < scan_lower or sub_upper > scan_upper:
            raise ValueError(
                f"FilterBank subband {sub_lower:.1f}-{sub_upper:.1f} Hz "
                f"does not fit within scan band {scan_lower:.1f}-{scan_upper:.1f} Hz."
            )

        return sub_center_freq, sub_bandwidth


def main() -> None:

    import pprint
    from util.format import fmt_title

    filterbank = FilterBank()
    fmt_title("FilterBank Parameters")
    pprint.pprint(filterbank.to_dict(), indent=4)


if __name__ == "__main__":
    main()
