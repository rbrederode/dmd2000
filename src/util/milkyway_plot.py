"""Plot an observation scan's line of sight on an illustration of the Milky Way.

From src: python -m util.milkyway_plot --obs OBS_ID --scan SCAN_ID --dir PATH
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from obs.obs_pointing import add_pointing_arguments, resolve_pointing_context

IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "milky_way.jpg"
# Registration against the bundled 5600-square illustration (not a survey map).
# Its longitude grid converges at the Sun, about 69.15% down the image.
SUN_Y_FRACTION = 0.6915
IMAGE_EXTENT = (-0.5, 0.5, SUN_Y_FRACTION - 1, SUN_Y_FRACTION)


def sightline_endpoint(longitude_deg):
    """Intersect a Sun-centred longitude ray with an inset image boundary.

    Longitude 0 points upwards towards the Galactic centre; 90 points left,
    matching the annotated illustration. The length carries no distance meaning.
    """
    longitude = np.deg2rad(longitude_deg)
    dx, dy = -np.sin(longitude), np.cos(longitude)
    xmin, xmax, ymin, ymax = IMAGE_EXTENT
    limits = []
    if abs(dx) > 1e-12:
        limits.append(((xmax - 0.04) if dx > 0 else (xmin + 0.04)) / dx)
    if abs(dy) > 1e-12:
        limits.append(((ymax - 0.04) if dy > 0 else (ymin + 0.04)) / dy)
    length = min(limits)
    return float(dx * length), float(dy * length)


def plot_sightline(galactic, observed_at, obs_id, scan_id, output_path, show=True, context=None):
    """Save/show an illustrated face-on projection of the scan's sightline."""
    import matplotlib.pyplot as plt

    image = plt.imread(IMAGE_PATH)
    figure, axes = plt.subplots(figsize=(10, 11), facecolor="black")
    axes.imshow(image, extent=IMAGE_EXTENT, origin="upper", aspect="equal")
    longitude, latitude = float(galactic.l.deg), float(galactic.b.deg)
    axes.plot(0, 0, "o", color="gold", markersize=7, zorder=4)
    if abs(np.cos(np.deg2rad(latitude))) > 1e-8:
        endpoint = sightline_endpoint(longitude)
        axes.annotate("", xy=endpoint, xytext=(0, 0),
                      arrowprops=dict(arrowstyle="-|>", color="red", linewidth=2.5,
                                      linestyle="--", mutation_scale=18), zorder=5)
    else:
        axes.text(0, 0.04, "Looking perpendicular to Galactic plane", ha="center", color="white",
                  bbox=dict(facecolor="black", alpha=0.8, edgecolor="none"))
    axes.set_xlim(*IMAGE_EXTENT[:2])
    axes.set_ylim(*IMAGE_EXTENT[2:])
    axes.axis("off")
    title = "Milky Way — Target Line of Sight"
    if scan_id is not None:
        title += f"\n{scan_id}"
    figure.suptitle(title, color="white", fontsize=11, y=0.97)
    time_label = "Scan midpoint UTC" if scan_id is not None else "Datetime UTC"
    figure.text(0.5, 0.915,
                f"l={longitude:.3f}°, b={latitude:+.3f}° | {time_label}: {observed_at.isoformat()}",
                ha="center", color="white", fontsize=9)
    if context is not None:
        latitude_site, longitude_site, height = context.location
        figure.text(0.5, 0.895, f"Site: {latitude_site:.6f}°, {longitude_site:.6f}°, {height:g} m",
                    ha="center", color="white", fontsize=9)
    figure.text(0.5, 0.065,
                "Red arrow: direction from the Sun, projected onto the Galactic plane.\n"
                "Illustration only; arrow length does not indicate target distance.",
                ha="center", color="white", fontsize=9)
    figure.text(0.5, 0.025, "Image: NASA/JPL-Caltech/ESO/R. Hurt (eso1339e)",
                ha="center", color="0.75", fontsize=8)
    figure.subplots_adjust(left=0.03, right=0.97, bottom=0.11, top=0.87)
    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=160, facecolor=figure.get_facecolor())
    if context is not None:
        output_path.with_suffix(".json").write_text(json.dumps(context.metadata(), indent=2) + "\n", encoding="utf-8")
    if show:
        plt.show()
    return figure


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    add_pointing_arguments(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        context = resolve_pointing_context(args)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, KeyError) as exc:
        parser.exit(1, f"Milky Way metadata lookup failed: {exc}\n")
    try:
        galactic, observed_at = context.galactic, context.observed_at
        output = Path(args.output_dir or args.dir).expanduser() / f"{context.output_name}-milkyway.png"
        figure = plot_sightline(galactic, observed_at, context.obs_id, context.scan_id, output,
                                show=not args.no_show, context=context)
        from matplotlib import pyplot as plt
        plt.close(figure)
        print(f"Galactic longitude: {galactic.l.deg:.4f}°; latitude: {galactic.b.deg:.4f}°")
        print(f"Saved: {output}")
        print(f"Saved: {output.with_suffix('.json')}")
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Milky Way plotting failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
