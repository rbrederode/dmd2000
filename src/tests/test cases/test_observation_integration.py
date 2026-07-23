from types import SimpleNamespace

import numpy as np

from obs.obs import Observation


def test_update_integrated_data_arrays_uses_loaded_seconds():
    observation = Observation.__new__(Observation)
    observation.obs_model = SimpleNamespace(obs_id="test-observation")
    key = (2, 0, 1)
    observation.int_data_arrays = {
        key: {
            "int_spr": np.zeros(4),
            "int_mpr": np.zeros(4),
            "int_tpw": [],
            "secs": 0.0,
            "scans": 0,
        }
    }

    scan = SimpleNamespace(
        scan_model=SimpleNamespace(
            tgt_idx=key[0],
            freq_scan=key[1],
            scan_iter=key[2],
            scan_id="test-scan",
        ),
        spr=np.ones((2, 4)),
        cal=np.ones((2, 4)),
        mpr=np.ones(4),
        get_loaded_seconds=lambda: 2,
    )

    observation._update_integrated_data_arrays(scan)

    assert observation.int_data_arrays[key]["secs"] == 2
    assert observation.int_data_arrays[key]["scans"] == 1
