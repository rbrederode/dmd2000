#!/usr/bin/env python3
"""
Utility to generate UML DOT files (via pyreverse) for every Python module in a
tree and optionally render those DOT files to PNG images.

Usage:
  python uml_tools.py generate --src src --dot-dir src/dot
  python uml_tools.py render --dot-dir src/dot --png-dir src/png
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List


def find_python_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under root, skipping cache directories."""
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def run_pyreverse(module_path: Path, output_dir: Path) -> Path:
    """Run pyreverse on a module and move resulting DOT file into output_dir."""
    module_name = module_path.stem
    cmd = ["pyreverse", str(module_path), "--filter-mode", "ALL", "-o", "dot", "-p", module_name]
    # "--all-ancestors", "--all-associated",

    subprocess.run(cmd, check=True, cwd=module_path.parent)

    # pyreverse emits classes_<project>.dot in the working directory.
    dot_filename = f"classes_{module_name}.dot"
    dot_path = module_path.parent / dot_filename
    if not dot_path.exists():
        # Fallback: pyreverse sometimes adds suffixes; pick the closest match.
        matches: List[Path] = list(module_path.parent.glob(f"classes_{module_name}*.dot"))
        if not matches:
            raise FileNotFoundError(
                f"Expected {dot_filename} (or variant) after running pyreverse on {module_path}"
            )
        dot_path = matches[0]

    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = output_dir / dot_path.name
    shutil.move(str(dot_path), target_path)

    # Optionally keep related packages dot together.
    pkg_candidates = [
        module_path.parent / "packages.dot",
        module_path.parent / f"packages_{module_name}.dot",
    ]
    for pkg in pkg_candidates:
        if pkg.exists():
            shutil.move(str(pkg), output_dir / pkg.name)

    return target_path


def convert_dot_to_png(dot_path: Path, output_dir: Path) -> Path:
    """Render a DOT file to PNG using Graphviz dot."""
    module_name = dot_path.stem.replace("classes_", "", 1)
    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / f"{module_name}_classes.png"
    cmd = ["dot", "-Tpng", str(dot_path), "-o", str(png_path)]
    subprocess.run(cmd, check=True)
    return png_path


def generate_action(args: argparse.Namespace) -> None:
    src_root = Path(args.src).resolve()
    dot_dir = (src_root / args.dot_dir) if not Path(args.dot_dir).is_absolute() else Path(args.dot_dir)

    modules = list(find_python_files(src_root))
    if not modules:
        print(f"No Python files found under {src_root}", file=sys.stderr)
        return

    for module in modules:
        try:
            dst = run_pyreverse(module, dot_dir)
            print(f"[pyreverse] {module} -> {dst}")
        except subprocess.CalledProcessError as exc:
            print(f"[pyreverse] FAILED {module}: {exc}", file=sys.stderr)
        except FileNotFoundError as exc:
            print(f"[pyreverse] WARNING {exc}", file=sys.stderr)


def render_action(args: argparse.Namespace) -> None:
    dot_dir = Path(args.dot_dir).resolve()
    png_dir = (
        (dot_dir / args.png_dir)
        if args.png_dir and not Path(args.png_dir).is_absolute()
        else Path(args.png_dir).resolve()
        if args.png_dir
        else dot_dir
    )

    dot_files = sorted(dot_dir.glob("classes_*.dot"))
    if not dot_files:
        print(f"No dot files matching classes_*.dot found in {dot_dir}", file=sys.stderr)
        return

    for dot_file in dot_files:
        try:
            png = convert_dot_to_png(dot_file, png_dir)
            print(f"[dot] {dot_file} -> {png}")
        except subprocess.CalledProcessError as exc:
            print(f"[dot] FAILED {dot_file}: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate UML DOT/PNG files for Python modules.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Run pyreverse for every Python module.")
    generate.add_argument("--src", default="src", help="Root directory to scan (default: src).")
    generate.add_argument(
        "--dot-dir",
        default="dot",
        help="Directory (absolute or relative to --src) to collect DOT files (default: dot).",
    )
    generate.set_defaults(func=generate_action)

    render = subparsers.add_parser("render", help="Convert DOT files to PNG images.")
    render.add_argument(
        "--dot-dir",
        default="src/dot",
        help="Directory containing classes_*.dot files (default: src/dot).",
    )
    render.add_argument(
        "--png-dir",
        default=None,
        help="Output directory for PNG files (default: same as --dot-dir).",
    )
    render.set_defaults(func=render_action)

    return parser


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
