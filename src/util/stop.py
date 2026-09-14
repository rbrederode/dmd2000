"""Issue an emergency stop to a dish using a configured launch profile."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_DM_COMMAND_HOST = "127.0.0.1"
DEFAULT_DM_COMMAND_PORT = 60003


def resolve_repo_root(explicit_repo_root: str | None = None) -> Path:
    if explicit_repo_root:
        return Path(explicit_repo_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _launch_option(args: list[str], option: str, default=None):
    """Return the last value assigned to an option in launch arguments."""
    value = default
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == option:
            if index + 1 >= len(args):
                raise ValueError(f"Launch option '{option}' has no value")
            value = args[index + 1]
            index += 2
            continue
        if argument.startswith(f"{option}="):
            value = argument.split("=", 1)[1]
        index += 1
    return value


def build_stop_command(
    repo_root: Path,
    profile: str,
    dish_id: str,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """Resolve a profile and construct its ``util.cmd_app`` stop command."""
    if not profile or Path(profile).name != profile:
        raise ValueError(f"Invalid configuration profile name '{profile}'")

    src_dir = repo_root / "src"
    config_dir = src_dir / "config" / profile
    launch_config_path = config_dir / "LaunchConfig.json"
    dish_config_path = config_dir / "DishList.json"
    if not launch_config_path.is_file():
        raise ValueError(f"Profile '{profile}' has no LaunchConfig.json")
    if not dish_config_path.is_file():
        raise ValueError(f"Profile '{profile}' has no DishList.json")

    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from models.dsh import DishList
    from models.launch import LaunchConfigModel

    launch_config = LaunchConfigModel.load_from_disk(
        input_dir=str(config_dir), filename="LaunchConfig.json"
    )
    dish_config = DishList.load_from_disk(
        input_dir=str(config_dir), filename="DishList.json"
    )

    available_dishes = sorted(dish.dsh_id for dish in dish_config.dish_list)
    if dish_id not in available_dishes:
        available = ", ".join(available_dishes) or "<none>"
        raise ValueError(
            f"Dish '{dish_id}' is not configured in profile '{profile}'. "
            f"Available dishes: {available}"
        )

    dm_launches = [
        launch for launch in launch_config.launches if launch.app_name.lower() == "dm"
    ]
    if len(dm_launches) != 1:
        raise ValueError(
            f"Profile '{profile}' must contain exactly one Dish Manager launch "
            f"definition; found {len(dm_launches)}"
        )

    dm_args = dm_launches[0].args
    command_host = host or _launch_option(
        dm_args, "--cmd_host", DEFAULT_DM_COMMAND_HOST
    )
    if command_host in ("0.0.0.0", "::"):
        command_host = DEFAULT_DM_COMMAND_HOST

    configured_port = _launch_option(
        dm_args, "--cmd_port", str(DEFAULT_DM_COMMAND_PORT)
    )
    try:
        command_port = port if port is not None else int(configured_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Dish Manager command port '{configured_port}' is not an integer"
        ) from exc

    command = [
        sys.executable,
        "-m",
        "util.cmd_app",
        "--host",
        str(command_host),
        "--port",
        str(command_port),
        "--system",
        "dm",
        "--timeout",
        str(timeout),
    ]

    command_config = _launch_option(dm_args, "--cmd_config")
    if command_config:
        command_config_path = Path(command_config).expanduser()
        if not command_config_path.is_absolute():
            command_config_path = src_dir / command_config_path
        command.extend(["--cmd_config", str(command_config_path)])

    command.extend(["stop", dish_id])
    return command


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stop",
        description="Immediately stop a dish using its profile's Dish Manager",
    )
    parser.add_argument(
        "--profile", required=True, help="Configuration profile name, e.g. jodrell"
    )
    parser.add_argument("dish_id", help="Dish identifier, e.g. dish001")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the Dish Manager connection and response",
    )
    parser.add_argument(
        "--host", help="Override the profile's Dish Manager command host"
    )
    parser.add_argument(
        "--port", type=int, help="Override the profile's Dish Manager command port"
    )
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the resolved cmd_app command without sending it",
    )
    parser.add_argument("--repo-root", help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        command = build_stop_command(
            resolve_repo_root(args.repo_root),
            args.profile,
            args.dish_id,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    if args.print_command:
        print(shlex.join(command))
        return 0

    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
