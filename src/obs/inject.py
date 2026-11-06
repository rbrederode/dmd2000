"""Submit an observation definition to TM through the authenticated web app."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Support both ``python -m obs.inject`` and ``python obs/inject.py`` invocation from the src directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.oda import ObsList

DEFAULT_URL = "http://127.0.0.1:8000/webhook"
DEFAULT_TIMEOUT = 20.0

"""Match UTC time tokens such as ``{{NOW}}`` and ``{{NOW+5m}}``.

The optional integer is a positive offset in minutes from the single UTC
timestamp captured when the observation definition is injected.
"""
NOW_TOKEN_PATTERN = re.compile(r"\{\{NOW(?:\+(?P<minutes>\d+)m)?\}\}")

def replace_now_tokens(value, now: datetime | None = None):
    """Replace UTC time tokens in all strings in a JSON-compatible value.

    ``{{NOW}}`` uses the captured UTC time, while ``{{NOW+<n>m}}`` uses
    that same instant plus ``n`` minutes.
    """

    base_time = now or datetime.now(timezone.utc)
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)
    else:
        base_time = base_time.astimezone(timezone.utc)

    def replace(item):
        if isinstance(item, str):
            def token_value(match: re.Match) -> str:
                minutes = int(match.group("minutes") or 0)
                token_time = base_time + timedelta(minutes=minutes)
                return token_time.strftime("%Y-%m-%dT%H%M%SZ")

            return NOW_TOKEN_PATTERN.sub(token_value, item)
        if isinstance(item, list):
            return [replace(child) for child in item]
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        return item

    return replace(value)

def build_webhook_payload(observation_file: str | Path, now: datetime | None = None) -> tuple[dict, list[str]]:
    """Load, validate and wrap an observation file for the web app's REST call.
        Params:
            observation_file: Path to the observation definition JSON file.
        Returns:
            A tuple containing the webhook payload and a list of observation IDs.
    """

    injection_time = now or datetime.now(timezone.utc)
    if injection_time.tzinfo is None:
        injection_time = injection_time.replace(tzinfo=timezone.utc)
    else:
        injection_time = injection_time.astimezone(timezone.utc)
    obs_path = Path(observation_file).expanduser()
    if not obs_path.is_absolute():
        obs_path = Path.cwd() / obs_path

    with obs_path.open("r", encoding="utf-8") as observation_stream:
        observation_data = json.load(observation_stream)

    observation_data = replace_now_tokens(observation_data, injection_time)
    obs_list = ObsList.from_data(observation_data, now=injection_time)
    obs_ids = [obs.obs_id for obs in obs_list.obs_list]

    return ({   "event": "alston-rt.ui.odt",
                "timestamp": injection_time.isoformat(),
                "message": obs_list.to_dict(),
            },
            obs_ids)

def inject_observation(observation_file: str | Path, url: str = DEFAULT_URL,
                       timeout: float = DEFAULT_TIMEOUT, token: str | None = None) -> tuple[dict, list[str]]:
    """POST an observation definition to the web app and await TM's result.
        Params:
            observation_file: Path to the observation definition JSON file.
            url: Web app webhook URL (default: http://127.0.0.1:8000/webhook).
            timeout: HTTP request timeout in seconds (default: 20).
            token: Authentication token; defaults to WEBHOOK_SECRET.
        Returns:
            A tuple containing the ResponseEvent JSON and observation IDs.
    """
    
    token = token if token is not None else os.getenv('WEBHOOK_SECRET')
    if not token:
        raise RuntimeError('Set WEBHOOK_SECRET or supply --token to authenticate with the web application')
    payload, obs_ids = build_webhook_payload(observation_file)
    request = Request(url=url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json", "X-Webhook-Token": token}, method="POST")

    with urlopen(request, timeout=timeout) as response:
        response_data = response.read().decode("utf-8")

    try:
        result = json.loads(response_data)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Web application returned invalid JSON') from exc

    if (not isinstance(result, dict) or result.get('status') not in ('success', 'error')
            or not isinstance(result.get('message'), str)):
        raise RuntimeError('Web application returned an invalid ResponseEvent')

    if result.get("status") != "success":
        context = ', '.join(f'{key}={result[key]}' for key in ('code', 'request_id') if result.get(key))
        raise RuntimeError(result['message'] + (f' ({context})' if context else ''))

    return result, obs_ids

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """ Parse command-line arguments for the observation injection script.
        Params:
            argv: List of command-line arguments (default: None, which uses sys.argv). 
        Returns:
            An argparse.Namespace object containing the parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Submit an observation definition JSON file to TM through the web application's REST endpoint.",
        epilog=(
            'UTC time tokens may be embedded in JSON strings, for example '
            '"obs_id": "ODT-{{NOW}}-dish001-1m". {{NOW}} is the injection '
            "time and {{NOW+<n>m}} is the injection time plus n minutes."
        ),
    )
    
    parser.add_argument("-f", "--file", required=True, type=Path, help="Observation definition JSON file (ObsModel, ObsList, or raw observation list).")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Web application webhook URL (default: {DEFAULT_URL}).")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT, type=float, help="HTTP request timeout in seconds (default: 20).")
    parser.add_argument("--token", help="Web authentication token; defaults to the WEBHOOK_SECRET environment variable.")

    return parser.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        result, obs_ids = inject_observation(args.file, url=args.url, timeout=args.timeout, token=args.token)
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        print(f"Web application returned HTTP {exc.code}: {response_body}", file=sys.stderr)
        return 2
    except URLError as exc:
        print(f"Could not connect to web application at {args.url}: {exc.reason}", file=sys.stderr)
        return 2
    except TimeoutError:
        print('HTTP request timed out; observation submission outcome is unknown', file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Observation submission failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Failed to load observation file {args.file}: {exc}", file=sys.stderr)
        return 1

    obs_label = ", ".join(obs_ids) if obs_ids else "(empty observation list)"
    print(f"Submitted observation definition through web application: {obs_label}")
    print(result['message'])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
