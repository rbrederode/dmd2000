#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def resolve_repo_root(explicit_repo_root: str | None) -> Path:
    if explicit_repo_root:
        return Path(explicit_repo_root).expanduser().resolve()

    cwd = Path.cwd()
    if (cwd / "src").is_dir() and (cwd / "scripts").is_dir():
        return cwd

    return Path(__file__).resolve().parents[2]


def expand_launch_value(value: str) -> str:
    if value.startswith("~"):
        return os.path.expanduser(value)
    return value


def resolve_entity_ids(positional_entity_ids: list[str], option_entity_ids: list[str] | None) -> list[str]:
    entity_ids = []

    if option_entity_ids:
        entity_ids.extend(option_entity_ids)

    if positional_entity_ids:
        entity_ids.extend(positional_entity_ids)

    if not entity_ids:
        raise ValueError("An entity identifier is required.")

    return entity_ids


def build_launch_command(repo_root: Path, launch, print_command: bool = False, keep_open: bool = True) -> list[str]:
    launcher = repo_root / "scripts" / "launch" / "launch_app.sh"
    command = [
        str(launcher),
        "--title",
        launch.entity_id,
        "--session-dir",
        "src",
        "--repo-root",
        str(repo_root),
    ]

    if print_command:
        command.append("--print-command")

    if not keep_open:
        command.append("--no-keep-open")

    for key, value in launch.env.items():
        command.extend(["--env", f"{key}={expand_launch_value(value)}"])

    launch_args = [expand_launch_value(item) for item in launch.args]
    command.extend(["--", launch.program, *launch_args])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch an application from a profile launch definition.")
    parser.add_argument("--profile", required=True, help="Configuration profile name, e.g. jodrell")
    parser.add_argument("entity_ids", nargs="*", help="Launch entity identifiers, e.g. dig001 tm001")
    parser.add_argument("--entity", "--entity_id", dest="entity_options", action="append", help="Launch entity identifier, e.g. dig001. May be repeated.")
    parser.add_argument("--repo-root", required=False, help="Optional override for repository root")
    parser.add_argument("--print-command", action="store_true", help="Print the resolved launch command without starting a terminal")
    parser.add_argument("--no-keep-open", action="store_true", help="Do not keep the launched terminal session open after the program exits")
    parser.add_argument("--launch-delay", type=float, default=0.2, help="Delay in seconds between launching multiple entities")

    args = parser.parse_args()

    try:
        entity_ids = resolve_entity_ids(args.entity_ids, args.entity_options)
    except ValueError:
        parser.error("At least one entity identifier is required. Provide one or more via --entity <id> and/or positional arguments.")

    repo_root = resolve_repo_root(args.repo_root)
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from models.launch import LaunchConfigModel

    config_dir = src_dir / "config" / args.profile
    config = LaunchConfigModel.load_from_disk(input_dir=str(config_dir), filename="LaunchConfig.json")
    available = ", ".join(sorted(item.entity_id for item in config.launches))

    exit_code = 0

    for index, entity_id in enumerate(entity_ids):
        launch = config.get_launch_by_entity_id(entity_id)

        if launch is None:
            parser.error(
                f"No launch definition found for entity_id '{entity_id}' in profile '{args.profile}'. "
                f"Available entity_ids: {available if available else '<none>'}"
            )

        command = build_launch_command(
            repo_root=repo_root,
            launch=launch,
            print_command=args.print_command,
            keep_open=not args.no_keep_open,
        )

        completed = subprocess.run(command, check=False)
        exit_code = completed.returncode if completed.returncode != 0 else exit_code

        if index < len(entity_ids) - 1 and not args.print_command and args.launch_delay > 0:
            time.sleep(args.launch_delay)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
