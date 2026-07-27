"""Create full-sky maps of HI-line signal-to-noise measurements.

This module converts DMD2000 scan products into coloured cells on a Mollweide
projection of galactic longitude and latitude. For each scan, it:

1. Loads the scan metadata and its corresponding QA metadata.
2. Selects an SNR measurement from the requested QA processing pipeline.
3. Uses the scan target index to find the target in the observation definition.
4. Converts the target coordinates to the galactic coordinate system.
5. Draws the resulting longitude, latitude, and SNR as a cell on the sky map.

Targets containing a ``sky_coord`` are transformed directly to galactic
coordinates. Fixed Alt/Az targets are converted using the scan start time and
the telescope latitude and longitude stored in the observation definition.

The module can be imported to build maps programmatically or run as a command-
line utility. When no scan metadata files are provided, it generates synthetic
data so the plotting layout can be demonstrated independently of an
observation.
"""

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
    """ Convert galactic longitude to the orientation used by sky maps.

        Params:
            lon: float or numpy.ndarray
                Longitude in degrees, normally in the interval [0, 360].

        Returns:
            float or numpy.ndarray: Longitude wrapped to [-180, 180] and
                sign-reversed so it increases from right to left.
    """
    lon = ((lon + 180) % 360) - 180
    return -lon

def galactic_bin_edges(l_step=10, b_step=10):
    """ Create longitude and latitude bin edges covering the whole sky.

        This helper does not validate the step sizes. Call ``_validate_steps``
        first when accepting values from an external caller.

        Params:
            l_step: float
                Galactic longitude bin width in degrees.
            b_step: float
                Galactic latitude bin height in degrees.

        Returns:
            tuple[numpy.ndarray, numpy.ndarray]: Longitude edges from 0 to 360
                degrees and latitude edges from -90 to +90 degrees.
    """
    l_edges = np.arange(0, 360 + l_step, l_step)
    b_edges = np.arange(-90, 90 + b_step, b_step)
    return l_edges, b_edges

def _validate_steps(l_step, b_step):
    """ Validate that the requested cells tile the sky without partial bins.

        Both steps must be positive. Longitude bins must divide 360 degrees
        exactly, and latitude bins must divide 180 degrees exactly.

        Params:
            l_step: float
                Galactic longitude bin width in degrees.
            b_step: float
                Galactic latitude bin height in degrees.

        Returns:
            None

        Raises:
            ValueError: If either step is non-positive or does not divide its
                angular range exactly.
    """
    if l_step <= 0 or b_step <= 0:
        raise ValueError("l_step and b_step must be positive.")
    if 360 % l_step != 0:
        raise ValueError("l_step must divide 360 exactly.")
    if 180 % b_step != 0:
        raise ValueError("b_step must divide 180 exactly.")

def _make_cell_polygons(l, b, l_step, b_step):
    """ Build one or more Mollweide polygons for a galactic map cell.

        Latitude is clipped at the celestial poles. Cells crossing the 0/360
        degree boundary or the projection boundary at 180 degrees are split
        so Matplotlib does not draw them across the entire map.

        Params:
            l: float
                Galactic longitude of the cell centre in degrees.
            b: float
                Galactic latitude of the cell centre in degrees.
            l_step: float
                Width of the cell in degrees.
            b_step: float
                Height of the cell in degrees.

        Returns:
            list[matplotlib.patches.Polygon]: Plot-ready cell polygons whose
                coordinates are expressed in radians.
    """
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
    """ Plot HI-line signal-to-noise cells on a Mollweide sky map.

        Params:
            cells: iterable[dict]
                Cells containing ``l`` and ``b`` centres in degrees and an
                ``snr`` value. Extra metadata fields are ignored.
            l_step: float
                Width of every plotted cell in degrees.
            b_step: float
                Height of every plotted cell in degrees.
            cmap: str
                Name of the Matplotlib colour map used for SNR values.
            title: str or None
                Optional title displayed above the map.
            snr_limits: tuple[float, float] or None
                Optional fixed minimum and maximum for the colour scale.

        Returns:
            tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]: Generated
                figure and Mollweide axes.

        Raises:
            ValueError: If the step sizes are invalid or no cells are supplied.
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
    """ Convert one DMD2000 scan metadata file into a plottable SNR cell.

        The function loads the scan model, finds its QA file, selects an SNR
        measurement, resolves the target through the observation definition,
        and converts that target to galactic coordinates.

        Params:
            scan_meta_path: str or Path
                Path to a DMD2000 ``*-meta.json`` scan metadata file.
            obs_path: str or Path
                Observation definition containing the target and telescope
                location.
            qa_path: str or Path or None
                Explicit QA metadata path. If omitted, it is derived from the
                scan metadata filename.
            qa_pipeline: str
                QA result group to use: ``mpr``, ``cal``, or ``spr``.
            qa_idx: int or None
                Entry selected from the ``cal`` or ``spr`` QA arrays. ``None``
                selects the first entry containing an SNR value.

        Returns:
            dict: Galactic coordinates, SNR, scan ID, observation ID, and
                target index for one map cell.

        Raises:
            ValueError: If required files, QA values, targets, times, or
                coordinates are missing.
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
    """ Convert several scan metadata files into map cells.

        Files with missing or unusable metadata are reported and skipped so
        the remaining scans can still be plotted.

        Params:
            scan_meta_paths: iterable[str or Path]
                Scan metadata files to convert.
            obs_path: str or Path
                Observation definition used to resolve scan targets.
            qa_pipeline: str
                QA result group to use: ``mpr``, ``cal``, or ``spr``.
            qa_idx: int or None
                Entry selected from the ``cal`` or ``spr`` QA arrays.

        Returns:
            list[dict]: Successfully converted cells in input-file order.
    """
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
    """ Load scan files and plot their QA SNR values in one operation.

        This convenience wrapper combines
        ``scan_metadata_files_to_hi_snr_cells`` with ``plot_hi_snr_map``.

        Params:
            scan_meta_paths: iterable[str or Path]
                Scan metadata files to plot.
            obs_path: str or Path
                Observation definition used to resolve scan targets.
            l_step: float
                Width of every plotted cell in degrees.
            b_step: float
                Height of every plotted cell in degrees.
            qa_pipeline: str
                QA result group to use: ``mpr``, ``cal``, or ``spr``.
            qa_idx: int or None
                Entry selected from the ``cal`` or ``spr`` QA arrays.
            cmap: str
                Name of the Matplotlib colour map used for SNR values.
            title: str or None
                Optional title displayed above the map.
            snr_limits: tuple[float, float] or None
                Optional fixed minimum and maximum for the colour scale.

        Returns:
            tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]: Generated
                figure and Mollweide axes.
    """
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
    """ Deserialize a scan metadata file into a ``ScanModel``.

        Params:
            scan_meta_path: str or Path
                Path to the scan ``*-meta.json`` file.

        Returns:
            ScanModel: Deserialized scan metadata model.
    """
    with open(scan_meta_path, "r") as f:
        return ScanModel.from_dict(json.load(f))

def _matching_qa_path(scan_meta_path):
    """ Derive the conventional QA path for a scan metadata file.

        A standard ``<prefix>-meta.json`` filename becomes
        ``<prefix>-qa.json``. Other filenames receive ``-qa.json`` after
        their stem.

        Params:
            scan_meta_path: str or Path
                Path to the scan metadata file.

        Returns:
            Path: Expected path of the corresponding QA metadata file.
    """
    scan_meta_path = Path(scan_meta_path)
    if scan_meta_path.name.endswith("-meta.json"):
        return scan_meta_path.with_name(scan_meta_path.name.removesuffix("-meta.json") + "-qa.json")
    return scan_meta_path.with_name(scan_meta_path.stem + "-qa.json")

def _load_scan_qa(qa_path):
    """ Load and validate a scan QA metadata file.

        Params:
            qa_path: str or Path
                Path to the ``*-qa.json`` file.

        Returns:
            ScanQA: Deserialized scan QA model.

        Raises:
            ValueError: If the file does not exist or contains an empty JSON
                value.
    """
    qa_path = Path(qa_path)
    if not qa_path.exists():
        raise ValueError(f"QA metadata file not found: {qa_path}")

    with open(qa_path, "r") as f:
        qa_meta = json.load(f)

    if not qa_meta:
        raise ValueError(f"QA metadata file is empty: {qa_path}")

    return ScanQA.from_dict(qa_meta)

def _extract_snr_db(scan_qa, pipeline="mpr", idx=0):
    """ Select an SNR value from one of a scan's QA processing stages.

        ``mpr`` stores one result directly. ``cal`` and ``spr`` store lists,
        so ``idx`` selects an entry. An ``idx`` of ``None`` selects the first
        usable SNR result.

        Params:
            scan_qa: ScanQA
                QA model containing results from the processing pipelines.
            pipeline: str
                QA result group to use: ``mpr``, ``cal``, or ``spr``.
            idx: int or None
                Entry selected from the ``cal`` or ``spr`` result arrays.

        Returns:
            float: Signal-to-noise ratio in decibels.

        Raises:
            ValueError: If the pipeline is unknown or the selected result has
                no SNR value.
    """
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
    """ Safely select a QA result from a possibly sparse list.

        Params:
            values: list or None
                QA result list to search.
            idx: int or None
                Required list index. ``None`` selects the first usable item.

        Returns:
            object or None: Selected QA result, or ``None`` when the list is
                missing, the index is invalid, or no usable result exists.
    """
    if values is None:
        return None
    if idx is None:
        return next((item for item in values if item is not None and item.snr_db is not None), None)
    if idx < 0 or idx >= len(values):
        return None
    return values[idx]

def _scan_galactic_coordinates(scan_model, obs_path=None):
    """ Resolve a scan target and return its galactic longitude and latitude.

        Equatorial or other Astropy ``sky_coord`` targets are transformed directly
        to the galactic frame. Fixed Alt/Az targets additionally require the
        observation's Earth location and the scan ``read_start`` time because
        their sky position changes with time.

        Params:
            scan_model: ScanModel
                The scan model containing the target index and read_start time.
            obs_path: str or Path
                Path to the observation definition JSON file containing the
                target and telescope location. Required for Alt/Az targets.

        Returns:
            tuple[float, float]: Galactic longitude and latitude in degrees.

        Raises:
            ValueError: If the observation path, target, scan time, or usable
                target coordinates are missing.
    """
    if obs_path is None:
        raise ValueError("obs_path is required to work out galactic coordinates for this scan.")

    obs = ObsModel.load_from_disk(input_dir=str(Path(obs_path).parent), filename=Path(obs_path).name)
    target = _target_for_scan(obs, scan_model)

    # If the target has a sky_coord, we can convert it directly to galactic coordinates.
    if target.sky_coord is not None:
        galactic = target.sky_coord.transform_to("galactic")
        return galactic.l.deg, galactic.b.deg

    if target.altaz is not None:
        if scan_model.read_start is None:
            raise ValueError(f"Scan {scan_model.scan_id} has no read_start time for Alt/Az conversion.")

        location = EarthLocation(
            lat=float(obs.latitude) * u.deg,
            lon=float(obs.longitude) * u.deg,
            height=float(obs.height) * u.m,
        )
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
    """ Find the observation target whose index is referenced by a scan.

        Params:
            obs: ObsModel
                Observation containing the target definitions.
            scan_model: ScanModel
                Scan containing the target index to resolve.

        Returns:
            TargetModel: Target whose ``tgt_idx`` matches the scan.

        Raises:
            ValueError: If the observation contains no matching target.
    """
    matches = [target for target in obs.targets if target.tgt_idx == scan_model.tgt_idx]
    if not matches:
        raise ValueError(f"No target with tgt_idx={scan_model.tgt_idx} in observation {obs.obs_id}.")
    return matches[0]


def _coord_component(coord, name):
    """ Read a named coordinate from either a dictionary or an object.

        Params:
            coord: dict or object
                Coordinate container holding the requested component.
            name: str
                Component name, such as ``alt`` or ``az``.

        Returns:
            object: Value of the requested coordinate component.
    """
    if isinstance(coord, dict):
        return coord[name]
    return getattr(coord, name)

def _demo_cells(l_step, b_step):
    """ Generate deterministic synthetic SNR cells for demonstrating the map.

        The artificial signal is strongest near the galactic plane and
        includes longitude variation plus a local enhancement.

        Params:
            l_step: float
                Galactic longitude spacing in degrees.
            b_step: float
                Galactic latitude spacing in degrees.

        Returns:
            list[dict]: Synthetic cells containing ``l``, ``b``, and ``snr``.
    """
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
    """ Parse command-line options and display or save an HI-line SNR map.

        When ``--scan-meta`` files are provided, the map is built from their QA
        metadata. Otherwise, a synthetic full-sky demonstration map is produced.

        Example usage:
        python obs/dist_map.py --scan-meta ~/samples/solar/*-meta.json --obs-file ~/samples/solar/ODT-2026-07-19T131500Z-dish001-2h-obs.json

        Params:
            None

        Returns:
            None
    """
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
