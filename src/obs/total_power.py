"""Per-second and per-scan views of the same processed physical-scan powers."""
import csv
import json
from datetime import datetime, timedelta, timezone

import numpy as np
from matplotlib import pyplot as plt

from obs.obs_display import SCAN_COLOURS, save_velocity_figure


def _start_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def physical_power_records(observation, excluded=()):
    """Read already-calculated per-second powers without retaining scan spectra."""
    excluded = set(excluded)
    records = []
    for group in observation.obs_model.target_scans:
        for model in group.scans:
            if model.scan_id in excluded:
                continue
            entry = observation.int_data_arrays.get((model.tgt_idx, model.freq_scan, model.scan_iter), {})
            if not entry.get("scans"):
                continue
            power = np.asarray(entry.get("int_tpw", []), dtype=float)
            power = power[:min(len(power), int(entry.get("secs", len(power))))]
            if not len(power):
                continue
            records.append(dict(scan_id=model.scan_id, target_id=model.tgt_idx, freq_scan=model.freq_scan,
                                digitiser=model.dig_id, kind=model.scan_type.name, scan_iter=model.scan_iter,
                                start=_start_time(getattr(model, "read_start", None)), power=power,
                                center_freq=getattr(model, "center_freq", None),
                                sample_rate=getattr(model, "sample_rate", None),
                                channels=getattr(model, "spectral_resolution", None),
                                power_count=np.asarray(entry.get("power_count", [])),
                                power_channel=np.asarray(entry.get("power_channel", []))))
    return sorted(records, key=lambda record: (record["target_id"], record["freq_scan"], record["digitiser"],
                                               record["kind"], record["scan_iter"]))


def timeline_origin(records):
    """Use real elapsed time only when all contributing recordings have timestamps."""
    starts = [record["start"] for record in records]
    return min(starts) if starts and all(start is not None for start in starts) else None


def power_series(records, *, origin=None):
    """Group native readings and their scan means, preserving physical boundaries."""
    groups = {}
    for record in records:
        key = record["target_id"], record["freq_scan"], record["digitiser"], record["kind"]
        groups.setdefault(key, []).append(record)
    for key, batch in sorted(groups.items()):
        batch.sort(key=lambda record: (record["start"] if origin is not None else record["scan_iter"], record["scan_id"]))
        offset = 0
        segments = []
        for index, record in enumerate(batch, 1):
            power = record["power"]
            start = (record["start"]-origin).total_seconds() if origin is not None else offset
            seconds = start + np.arange(1, len(power)+1, dtype=float)
            finite = power[np.isfinite(power)]
            segments.append(dict(record=record, seconds=seconds, power=power, scan=index,
                                 mean=float(np.mean(finite)) if len(finite) else np.nan))
            offset += len(power)
        yield key, segments


def save_total_power(records, path, observation, *, resolution="scans", origin=None, baseline="none", basis="frequency"):
    """Plot each recorded second or its physical scan's mean, without resampling."""
    if resolution not in ("seconds", "scans"):
        raise ValueError("Total Power resolution must be seconds or scans")
    if not records:
        raise ValueError("No processed physical-scan total-power readings are available")
    figure, axis = plt.subplots(figsize=(14, 6))
    try:
        for index, (key, segments) in enumerate(power_series(records, origin=origin)):
            target, frequency, digitiser, kind = key
            colour = SCAN_COLOURS[index % len(SCAN_COLOURS)]
            style = ("-", "-.", ":", (0, (5, 1, 1, 1, 1, 1)))[(index // len(SCAN_COLOURS)) % 4]
            label = f"Tgt {target}, Freq {frequency} · {digitiser} · {kind}"
            if resolution == "seconds":
                for segment_index, segment in enumerate(segments):
                    axis.plot(segment["seconds"], segment["power"], color=colour, linestyle=style,
                              marker="." if len(segment["power"]) == 1 else None,
                              label=label if segment_index == 0 else "_nolegend_", linewidth=1.2)
            else:
                axis.plot([segment["scan"] for segment in segments], [segment["mean"] for segment in segments],
                          color=colour, linestyle=style, marker=".", label=label, linewidth=1.5)
            plotted = np.concatenate([segment["power"] for segment in segments]) if resolution == "seconds" else np.array([segment["mean"] for segment in segments])
            finite = plotted[np.isfinite(plotted)]
            if len(finite):
                axis.axhline(float(np.mean(finite)), color=colour, linestyle="--", linewidth=1, alpha=.45)
        xlabel = "Scan number" if resolution == "scans" else (
            "Elapsed observation time [s]" if origin is not None else "Recorded time [s] (gaps excluded)")
        axis.set(title=f"Observation {observation} — Total Power · Per {'second' if resolution == 'seconds' else 'scan'}",
                 xlabel=xlabel, ylabel="Total power [a.u.]")
        axis.set_title(axis.get_title() + f" ({basis.capitalize()} baseline: {baseline})")
        if resolution == "scans":
            from matplotlib.ticker import MaxNLocator
            axis.xaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(alpha=.25)
        save_velocity_figure(figure, axis, path)
    finally:
        plt.close(figure)


def save_power_csv(records, path, *, resolution="seconds", origin=None):
    """Export the exact plotted readings or scan means, with physical scan IDs."""
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["scan_id", "target_id", "freq_scan", "digitiser", "kind", "scan_number",
                         "second_in_scan", "elapsed_or_recorded_seconds", "sample_end_utc", "total_power"])
        for _, segments in power_series(records, origin=origin):
            for segment in segments:
                record = segment["record"]
                common = [record[key] for key in ("scan_id", "target_id", "freq_scan", "digitiser", "kind")]
                if resolution == "scans":
                    writer.writerow([*common, segment["scan"], "", "", "",
                                     segment["mean"] if np.isfinite(segment["mean"]) else ""])
                else:
                    for index, (second, power) in enumerate(zip(segment["seconds"], segment["power"]), 1):
                        timestamp = (record["start"] + timedelta(seconds=index)).astimezone(timezone.utc).isoformat() if record["start"] else ""
                        writer.writerow([*common, segment["scan"], index, float(second), timestamp,
                                         float(power) if np.isfinite(power) else ""])


def save_power_cache(records, folder):
    """Keep immutable native totals and affine reconstruction responses."""
    arrays, labels = {}, []
    for index, record in enumerate(records):
        label = {key: value for key, value in record.items()
                 if key not in ("power", "power_count", "power_channel", "start")}
        label["start"] = record["start"].isoformat() if record["start"] else None
        labels.append(label)
        for key in ("power", "power_count", "power_channel"):
            arrays[f"{index}_{key}"] = record[key]
    np.savez_compressed(folder / "native-total-power.npz", **arrays)
    (folder / "native-total-power.json").write_text(json.dumps(labels), encoding="utf-8")


def load_power_cache(folder):
    labels = json.loads((folder / "native-total-power.json").read_text())
    with np.load(folder / "native-total-power.npz", allow_pickle=False) as arrays:
        return [{**label, "start": _start_time(label["start"]),
                 **{key: arrays[f"{index}_{key}"].copy() for key in ("power", "power_count", "power_channel")}}
                for index, label in enumerate(labels)]


def corrected_power_records(records, spectra, baseline):
    """Apply the exact aggregate spectral correction to every recorded second.

    Supported baseline corrections are affine in frequency/channel index. Using
    cached reconstruction responses preserves the existing flag/RFI policy and
    real temporal variations, without fitting each second or accumulating edits.
    """
    output = []
    for record in records:
        if baseline == "none" or record["kind"] != "SKY":
            output.append({**record, "power": record["power"].copy()})
            continue
        matches = [spectrum for spectrum in spectra
                   if spectrum["target_id"] == record["target_id"]
                   and spectrum["freq_scan"] == record["freq_scan"]
                   and spectrum.get("digitiser", record["digitiser"]) == record["digitiser"]]
        if len(matches) != 1:
            raise ValueError("Total Power needs an available individual spectral baseline for each SKY aggregate.")
        spectrum = matches[0]
        if spectrum.get("frequency_mhz") is None:
            raise ValueError("Reprocess this observation to cache frequency channels for Total Power updates.")
        correction = np.ma.asarray(spectrum["frequency_correction"]).filled(np.nan)
        frequency = np.asarray(spectrum["frequency_mhz"])
        valid = np.flatnonzero(np.isfinite(correction) & np.isfinite(frequency))
        if len(valid) < 2:
            raise ValueError("Insufficient Frequency baseline channels for Total Power.")
        first, last = valid[0], valid[-1]
        slope = (correction[last] - correction[first]) / (frequency[last] - frequency[first])
        n = record["channels"]
        offsets = np.fft.fftshift(np.fft.fftfreq(n, d=1/record["sample_rate"]))
        f0 = (record["center_freq"] + offsets[0]) / 1e6
        intercept = correction[first] + slope * (f0 - frequency[first])
        channel_slope = slope * record["sample_rate"] / n / 1e6
        if any(record[key].shape != record["power"].shape for key in ("power_count", "power_channel")):
            raise ValueError("Reprocess this observation once to cache flag-aware Total Power corrections.")
        power = record["power"] - intercept * record["power_count"] - channel_slope * record["power_channel"]
        output.append({**record, "power": power})
    return output


def render_power_products(records, spectra, result, folder, baseline, *, basis="frequency"):
    """Refresh both resolutions, individual/all-SKY views and matching CSVs."""
    owners = [result, *result["aggregates"]]
    for owner in owners:
        for key in ("timeline", "timeline_seconds"):
            owner["views"].pop(key, None)
            owner.get("view_errors", {}).pop(key, None)
    try:
        records = corrected_power_records(records, spectra, baseline)
    except ValueError as exc:
        mark_power_unavailable(result, str(exc))
        return
    origin = timeline_origin(records)
    for resolution, key, suffix in (("scans", "timeline", "timeline"),
                                    ("seconds", "timeline_seconds", "timeline-seconds")):
        save_power_csv(records, folder / f"total-power-{resolution}.csv", resolution=resolution, origin=origin)
        for owner in owners:
            batch = [r for r in records if r["kind"] == "SKY"] if owner is result else [
                r for r in records if all(r.get(k) == owner.get(k) for k in ("target_id", "freq_scan", "digitiser", "kind"))]
            if not batch:
                continue
            filename = f"{owner['id']}-{suffix}.png" if owner is not result else f"{suffix}.png"
            save_total_power(batch, folder / filename, result["observation"], resolution=resolution,
                             origin=origin, baseline=baseline if any(r["kind"] == "SKY" for r in batch) else "none", basis=basis)
            owner["views"][key] = filename


def mark_power_unavailable(result, reason):
    for owner in [result, *result["aggregates"]]:
        for key in ("timeline", "timeline_seconds"):
            owner["views"].pop(key, None)
            owner.setdefault("view_errors", {})[key] = reason
    result.setdefault("warnings", []).append(reason)
