import argparse
import os
from pathlib import Path

import matplotlib
from blimpy import Waterfall


def _configure_matplotlib_backend() -> bool:
	"""Choose an interactive backend when the environment can support it."""
	has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
	if has_display:
		try:
			import tkinter  # noqa: F401
			matplotlib.use("TkAgg")
			return True
		except Exception:
			pass

	matplotlib.use("Agg")
	return False


INTERACTIVE_BACKEND = _configure_matplotlib_backend()

import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
	"""Parse command line arguments for the blimpy plotter utility."""
	parser = argparse.ArgumentParser(description="Open a SIGPROC .fil file and plot its waterfall view.")
	parser.add_argument(
		"--fil",
		type=str,
		default=None,
		help="Full path to the SIGPROC .fil file. If omitted, the script will prompt for it.",
	)
	return parser.parse_args()


def prompt_for_fil_path() -> Path:
	"""Prompt the user for a .fil file path and return it as a resolved Path."""
	raw_path = input("Enter the full path to the SIGPROC .fil file: ").strip().strip('"').strip("'")
	if not raw_path:
		raise ValueError("A .fil file path is required.")

	fil_path = Path(raw_path).expanduser()
	if not fil_path.exists():
		raise FileNotFoundError(f"File not found: {fil_path}")

	if fil_path.is_dir():
		raise IsADirectoryError(f"Expected a .fil file, got a directory: {fil_path}")

	return fil_path.resolve()


def main() -> None:
	args = parse_args()
	fil_path = Path(args.fil).expanduser().resolve() if args.fil else prompt_for_fil_path()
	waterfall = Waterfall(str(fil_path))
	png_path = fil_path.with_name(f"{fil_path.stem}-waterfall.png")

	waterfall.info()
	figure = waterfall.plot_waterfall()
	if figure is None:
		figure = plt.gcf()

	if figure is None or not figure.get_axes():
		raise RuntimeError("Waterfall plot was not created, so no PNG could be saved.")

	figure.savefig(png_path, dpi=150, bbox_inches="tight")
	print(f"Saved waterfall plot to: {png_path.resolve()}")

	if INTERACTIVE_BACKEND:
		plt.show()
	else:
		print("Interactive plotting is not available in this session, so the figure was saved to PNG instead.")
		print(f"Waterfall image saved to: {png_path.resolve()}")


if __name__ == "__main__":
	main()