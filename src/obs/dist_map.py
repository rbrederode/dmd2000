import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from matplotlib.collections import PatchCollection
from matplotlib.colors import Normalize

from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers
import astropy.units as u

from models.obs import ObsModel
from models.qa import ScanQA
from models.scan import ScanModel

iers.conf.auto_max_age = None


def wrap_lon_deg(lon):
    """
    Convert galactic longitude from [0, 360] to [-180, 180],
    then flip sign so longitude increases to the left, as in astronomy maps.
    """
    lon = ((lon + 180) % 360) - 180
    return -lon


def galactic_bin_edges(l_step=10, b_step=10):
    l_edges = np.arange(0, 360 + l_step, l_step)
    b_edges = np.arange(-90, 90 + b_step, b_step)
    return l_edges, b_edges


def _validate_steps(l_step, b_step):
    if l_step <= 0 or b_step <= 0:
        raise ValueError("l_step and b_step must be positive.")
    if 360 % l_step != 0:
        raise ValueError("l_step must divide 360 exactly.")
    if 180 % b_step != 0:
        raise ValueError("b_step must divide 180 exactly.")


def _make_cell_polygons(l, b, l_step, b_step):
    l = l % 360
    b0 = max(-90, b - b_step / 2)
    b1 = min(90, b + b_step / 2)

    l0 = l - l_step / 2
    l1 = l + l_step / 2

    lon_ranges = [(l0, l1)]
    if l0 < 0:
        lon_ranges = [(l0 + 360, 360), (0, l1)]
    elif l1 > 360:
        lon_ranges = [(l0, 360), (0, l1 - 360)]
    elif l0 < 180 < l1:
        lon_ranges = [(l0, 180), (180, l1)]

    polygons = []
    for lon0, lon1 in lon_ranges:
        corners_l = np.array([lon0, lon1, lon1, lon0])
        corners_b = np.array([b0, b0, b1, b1])

        x = np.radians(wrap_lon_deg(corners_l))
        y = np.radians(corners_b)
        polygons.append(Polygon(np.column_stack([x, y]), closed=True))

    return polygons


def plot_hi_snr_map(cells, l_step=10, b_step=10, cmap="viridis", title=None, snr_limits=None):
    """
    cells: iterable of dicts:
        {
            "l": galactic longitude bin centre in degrees,
            "b": galactic latitude bin centre in degrees,
            "snr": signal-to-noise value
        }
    """
    _validate_steps(l_step, b_step)

    fig = plt.figure(figsize=(12, 6))
    ax = fig.add_subplot(111, projection="mollweide")

    patches = []
    values = []

    for cell in cells:
        l = cell["l"]
        b = cell["b"]
        snr = cell["snr"]

        cell_polygons = _make_cell_polygons(l, b, l_step, b_step)
        patches.extend(cell_polygons)
        values.extend([snr] * len(cell_polygons))

    if not patches:
        raise ValueError("No cells were provided.")

    norm = None
    if snr_limits is not None:
        norm = Normalize(vmin=snr_limits[0], vmax=snr_limits[1])

    collection = PatchCollection(
        patches,
        array=np.array(values),
        cmap=cmap,
        norm=norm,
        edgecolor="white",
        linewidth=0.4
    )

    ax.add_collection(collection)

    ax.grid(True, alpha=0.4)
    ax.set_xlabel("Galactic longitude")
    ax.set_ylabel("Galactic latitude")
    if title:
        ax.set_title(title)

    cbar = fig.colorbar(collection, ax=ax, orientation="horizontal", pad=0.08)
    cbar.set_label("HI line signal-to-noise ratio")

    return fig, ax

def scan_metadata_to_hi_snr_cell(scan_meta_path, obs_path=None, qa_path=None, qa_pipeline="mpr", qa_idx=0):
    """
    Convert one dmd2000 scan metadata file into one map cell.

    This is the simple helper you normally want:
      1. Read a scan `*-meta.json` file.
      2. Read the matching `*-qa.json` file.
      3. Look up the scan target in the observation file.
      4. Convert the target to galactic longitude/latitude.
      5. Return {"l": ..., "b": ..., "snr": ...} for `plot_hi_snr_map`.

    `obs_path` is needed when the scan target is not already stored as galactic
    coordinates in the metadata. For zenith drift scans, the observation file
    also provides the dish latitude/longitude needed to convert Alt/Az to sky.
    """
    scan_meta_path = Path(scan_meta_path)
    scan_model = _load_scan_model(scan_meta_path)
    qa = _load_scan_qa(qa_path or _matching_qa_path(scan_meta_path))
    snr = _extract_snr_db(qa, pipeline=qa_pipeline, idx=qa_idx)
    l_deg, b_deg = _scan_galactic_coordinates(scan_model, obs_path=obs_path)

    return {
        "l": float(l_deg),
        "b": float(b_deg),
        "snr": float(snr),
        "scan_id": scan_model.scan_id,
        "obs_id": scan_model.obs_id,
        "tgt_idx": scan_model.tgt_idx,
    }


def scan_metadata_files_to_hi_snr_cells(scan_meta_paths, obs_path=None, qa_pipeline="mpr", qa_idx=0):
    cells = []
    for scan_meta_path in scan_meta_paths:
        try:
            cells.append(
                scan_metadata_to_hi_snr_cell(
                    scan_meta_path,
                    obs_path=obs_path,
                    qa_pipeline=qa_pipeline,
                    qa_idx=qa_idx,
                )
            )
        except ValueError as err:
            print(f"Skipping {scan_meta_path}: {err}")

    return cells


def plot_hi_snr_map_from_scan_metadata(
    scan_meta_paths,
    obs_path=None,
    l_step=10,
    b_step=10,
    qa_pipeline="mpr",
    qa_idx=0,
    cmap="viridis",
    title=None,
    snr_limits=None,
):
    cells = scan_metadata_files_to_hi_snr_cells(
        scan_meta_paths,
        obs_path=obs_path,
        qa_pipeline=qa_pipeline,
        qa_idx=qa_idx,
    )
    return plot_hi_snr_map(
        cells,
        l_step=l_step,
        b_step=b_step,
        cmap=cmap,
        title=title,
        snr_limits=snr_limits,
    )


def _load_scan_model(scan_meta_path):
    with open(scan_meta_path, "r") as f:
        return ScanModel.from_dict(json.load(f))


def _matching_qa_path(scan_meta_path):
    scan_meta_path = Path(scan_meta_path)
    if scan_meta_path.name.endswith("-meta.json"):
        return scan_meta_path.with_name(scan_meta_path.name.removesuffix("-meta.json") + "-qa.json")
    return scan_meta_path.with_name(scan_meta_path.stem + "-qa.json")


def _load_scan_qa(qa_path):
    qa_path = Path(qa_path)
    if not qa_path.exists():
        raise ValueError(f"QA metadata file not found: {qa_path}")

    with open(qa_path, "r") as f:
        qa_meta = json.load(f)

    if not qa_meta:
        raise ValueError(f"QA metadata file is empty: {qa_path}")

    return ScanQA.from_dict(qa_meta)


def _extract_snr_db(scan_qa, pipeline="mpr", idx=0):
    qa = None

    if pipeline == "mpr":
        qa = scan_qa.mpr_qa
    elif pipeline == "cal":
        qa = _qa_from_list(scan_qa.cal_qa, idx)
    elif pipeline == "spr":
        qa = _qa_from_list(scan_qa.spr_qa, idx)
    else:
        raise ValueError(f"Unknown QA pipeline: {pipeline}")

    if qa is None or qa.snr_db is None:
        raise ValueError(f"No SNR value found in {pipeline}_qa at index {idx}.")

    return qa.snr_db


def _qa_from_list(values, idx):
    if values is None:
        return None
    if idx is None:
        return next((item for item in values if item is not None and item.snr_db is not None), None)
    if idx < 0 or idx >= len(values):
        return None
    return values[idx]


def _scan_galactic_coordinates(scan_model, obs_path=None):
    if obs_path is None:
        raise ValueError("obs_path is required to work out galactic coordinates for this scan.")

    obs = ObsModel.load_from_disk(input_dir=str(Path(obs_path).parent), filename=Path(obs_path).name)
    target = _target_for_scan(obs, scan_model)

    if target.sky_coord is not None:
        galactic = target.sky_coord.transform_to("galactic")
        return galactic.l.deg, galactic.b.deg

    if target.altaz is not None:
        if scan_model.read_start is None:
            raise ValueError(f"Scan {scan_model.scan_id} has no read_start time for Alt/Az conversion.")

        location = EarthLocation(lat=float(obs.latitude) * u.deg, lon=float(obs.longitude) * u.deg)
        alt = _coord_component(target.altaz, "alt")
        az = _coord_component(target.altaz, "az")
        altaz = AltAz(
            alt=float(alt) * u.deg,
            az=float(az) * u.deg,
            obstime=Time(scan_model.read_start),
            location=location,
        )
        galactic = SkyCoord(altaz).transform_to("galactic")
        return galactic.l.deg, galactic.b.deg

    raise ValueError(f"Target {scan_model.tgt_idx} has neither sky_coord nor altaz coordinates.")


def _target_for_scan(obs, scan_model):
    matches = [target for target in obs.targets if target.tgt_idx == scan_model.tgt_idx]
    if not matches:
        raise ValueError(f"No target with tgt_idx={scan_model.tgt_idx} in observation {obs.obs_id}.")
    return matches[0]


def _coord_component(coord, name):
    if isinstance(coord, dict):
        return coord[name]
    return getattr(coord, name)


def _demo_cells(l_step, b_step):
    cells = []

    for l in np.arange(0, 360, l_step):
        for b in np.arange(-90 + b_step / 2, 90, b_step):
            plane_weight = np.exp(-0.5 * (b / 18.0) ** 2)
            longitude_structure = 0.5 + 0.5 * np.cos(np.radians(l - 30))
            local_enhancement = np.exp(-0.5 * (((l - 90) / 25.0) ** 2 + ((b + 15) / 15.0) ** 2))
            snr = 2.0 + 12.0 * plane_weight * longitude_structure + 8.0 * local_enhancement
            cells.append({"l": float(l), "b": float(b), "snr": float(snr)})

    return cells


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Plot an HI line SNR map in galactic coordinates.")
    parser.add_argument("--l-step", type=float, default=10.0, help="Galactic longitude bin size in degrees.")
    parser.add_argument("--b-step", type=float, default=10.0, help="Galactic latitude bin size in degrees.")
    parser.add_argument("--scan-meta", nargs="*", default=None, help="One or more dmd2000 *-meta.json scan metadata files.")
    parser.add_argument("--obs-file", type=str, default=None, help="Observation JSON file used to resolve target galactic coordinates.")
    parser.add_argument("--qa-pipeline", choices=["mpr", "cal", "spr"], default="mpr", help="QA pipeline to use for SNR.")
    parser.add_argument("--qa-idx", type=int, default=0, help="QA index to use for cal/spr arrays.")
    parser.add_argument("--output", type=str, default=None, help="Optional output image path. If omitted, show the plot.")
    args = parser.parse_args()

    if args.scan_meta:
        cells = scan_metadata_files_to_hi_snr_cells(
            args.scan_meta,
            obs_path=args.obs_file,
            qa_pipeline=args.qa_pipeline,
            qa_idx=args.qa_idx,
        )
        title = f"HI Line SNR Map ({args.l_step:g} deg x {args.b_step:g} deg)"
    else:
        cells = _demo_cells(args.l_step, args.b_step)
        title = f"Demo HI Line SNR Map ({args.l_step:g} deg x {args.b_step:g} deg)"

    fig, ax = plot_hi_snr_map(
        cells,
        l_step=args.l_step,
        b_step=args.b_step,
        title=title,
    )

    if args.output:
        fig.savefig(args.output, dpi=160, bbox_inches="tight")
    else:
        plt.show()


if __name__ == "__main__":
    main()
