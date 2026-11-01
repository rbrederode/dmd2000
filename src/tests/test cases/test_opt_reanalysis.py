import csv
import json

import matplotlib
matplotlib.use("Agg")
import numpy as np

from ui.opt_reanalysis import reanalyse
from ui.opt_service import atomic_json


def test_reanalysis_preserves_native_powers_and_signal_products(tmp_path, monkeypatch):
    import obs.opt
    monkeypatch.setattr(obs.opt, "process_observation", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No recorded scan replay")))
    source, out = tmp_path / "source", tmp_path / "updated"
    source.mkdir(); out.mkdir()
    velocity = np.linspace(-100, 150, 251)
    original = 10 + .01 * velocity + 2 * np.exp(-((velocity - 10) / 10)**2)
    original[20] = np.nan
    arrays, labels, aggregates = {}, [], []
    for i in range(2):
        identifier = f"aggregate-{i:04d}"
        arrays[f"{i}_velocity"] = velocity
        arrays[f"{i}_power"] = original + i
        labels.append(dict(id=identifier, target_id=i, freq_scan=0, center_freq=1420e6, longitude=30 + i*10, latitude=0))
        aggregates.append(dict(id=identifier, target_id=i, freq_scan=0, views={"signal": f"{identifier}-signal.png"}))
        (source / f"{identifier}-signal.png").write_bytes(b"unchanged signal image")
    np.savez_compressed(source / "native-spectra.npz", **arrays)
    atomic_json(source / "native-spectra.json", labels)
    atomic_json(source / "result.json", dict(observation="example", aggregates=aggregates,
                                            views={}, warnings=[], files=[]))
    messages = []
    result = reanalyse(dict(source_results=str(source), settings=dict(baseline="linear", baseline_vmin=-100,
                       baseline_vmax=150, contours=None, contour_smoothing=2)), out, lambda m, f: messages.append(m))
    assert result["analysis_only"]
    assert result["views"]["lv"] == "lv.png"
    assert result["views"]["velocity"] == "velocity.png"
    assert (out / "aggregate-0000-signal.png").read_bytes() == b"unchanged signal image"
    assert (out / "native-spectra.npz").read_bytes() == (source / "native-spectra.npz").read_bytes()
    with (out / "velocity.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 502
    first = rows[:251]
    assert first[20]["calibrated_power"] == first[20]["baseline_subtracted_power"] == ""
    valid = np.isfinite(original)
    np.testing.assert_allclose([float(first[i]["calibrated_power"]) for i in np.flatnonzero(valid)], original[valid])
    corrected = np.array([float(row["baseline_subtracted_power"]) if row["baseline_subtracted_power"] else np.nan for row in first])
    assert np.nanmax(corrected) < 3
    assert messages
