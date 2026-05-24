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
        "sub_bandwidth": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and v >= 0.0)), # Output sub-bandwidth in Hz for the generated filterbank product
        "temporal_resolution": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and 1.0 <= v <= 1000.0)),
        "gap_mean_duration": And(Or(None, int, float), lambda v: v is None or (isinstance(v, (int, float)) and 0.0 <= v <= 1000.0)), # Duration either side of a data gap to average when filling gaps (milliseconds)
        "dtype": And(str, lambda v: v in ["uint8", "uint16", "float32", "float64"]), # Data type for filterbank output (e.g. uint16)
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "FilterBank",
            "enabled": False,                       # Whether filterbank file creation is enabled
            "sub_bandwidth": None,                   # Output sub-bandwidth in Hz for the generated filterbank product
            "temporal_resolution": None,            # Time resolution (milliseconds) for summing power spectra (e.g. 1 millisecond)
            "gap_mean_duration": 1.0,               # Duration either side of a data gap to average when filling gaps (milliseconds)
            "dtype": "uint8",                       # Data type for filterbank output (e.g. uint16)
         }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)


def main() -> None:

    import pprint
    from util.format import fmt_title

    filterbank = FilterBank()
    fmt_title("FilterBank Parameters")
    pprint.pprint(filterbank.to_dict(), indent=4)


if __name__ == "__main__":
    main()
