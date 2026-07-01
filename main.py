#!/usr/bin/env python3
"""Runner for LOR reaction-rate avatar updates.

Default runtime behavior:
  * run forever;
  * run immediately on start;
  * then sleep at least 120 minutes between iterations;
  * never run more frequently than once per 2 hours, even if config asks for less.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

from libs.connection import ConnectionError
from libs.lor_client import LorClient, LorError


DEFAULT_INTERVAL_MINUTES = 120.0
MIN_INTERVAL_MINUTES = 120.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LOR reaction-rate avatar updater")
    parser.add_argument("--user-config", default="configs/user.yml", help="Path to configs/user.yml")
    parser.add_argument("--connection-config", default="configs/conn.yml", help="Path to configs/conn.yml")
    parser.add_argument("--once", action="store_true", help="Run one scan/update iteration and exit")
    parser.add_argument("--login", action="store_true", help="Only log in and save cookies")
    parser.add_argument("--print-proxy", action="store_true", help="Print connection proxy status")
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=None,
        help="Minutes between iterations. Values below 120 are clamped to 120.",
    )
    return parser


def stats_to_dict(stats: Any) -> dict[str, object]:
    return {
        "counts": stats.counts,
        "rates": stats.rates,
        "avatar_path": str(stats.avatar_path) if stats.avatar_path else "",
        "uploaded": stats.uploaded,
        "changed": stats.changed,
    }


def read_yaml_mapping(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def runner_value(raw: dict[str, Any], dash_key: str, underscore_key: str, default: Any = None) -> Any:
    runner = raw.get("runner") or {}
    if not isinstance(runner, dict):
        return default
    if dash_key in runner:
        return runner[dash_key]
    if underscore_key in runner:
        return runner[underscore_key]
    return default


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def resolve_interval_minutes(raw_config: dict[str, Any], cli_interval_minutes: float | None) -> float:
    """Return interval in minutes, with a hard lower bound of 120 minutes."""
    if cli_interval_minutes is not None:
        requested = cli_interval_minutes
        source = "CLI --interval-minutes"
    else:
        interval_raw = runner_value(raw_config, "interval-minutes", "interval_minutes", None)
        if interval_raw is not None:
            requested = as_float(interval_raw, DEFAULT_INTERVAL_MINUTES)
            source = "configs/user.yml runner.interval-minutes"
        else:
            # Backward compatibility with old configs. It is still clamped to the
            # project safety limit, so runs-per-hour: 4 becomes 120 minutes.
            runs_per_hour_raw = runner_value(raw_config, "runs-per-hour", "runs_per_hour", None)
            if runs_per_hour_raw is not None:
                runs_per_hour = max(1, as_int(runs_per_hour_raw, 1))
                requested = 60.0 / runs_per_hour
                source = "configs/user.yml runner.runs-per-hour"
            else:
                requested = DEFAULT_INTERVAL_MINUTES
                source = "default"

    if requested < MIN_INTERVAL_MINUTES:
        print(
            f"WARNING: requested interval {requested:g} min from {source}; "
            f"clamped to {MIN_INTERVAL_MINUTES:g} min",
            file=sys.stderr,
            flush=True,
        )
        requested = MIN_INTERVAL_MINUTES
    return requested


def resolve_max_runs(raw_config: dict[str, Any], once: bool) -> int:
    if once:
        return 1
    return max(0, as_int(runner_value(raw_config, "max-runs", "max_runs", 0), 0))


def resolve_run_on_start(raw_config: dict[str, Any], once: bool) -> bool:
    if once:
        return True
    return as_bool(runner_value(raw_config, "run-on-start", "run_on_start", True), True)


def local_time_text(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(time.time() if ts is None else ts))


def sleep_between_runs(interval_seconds: float) -> None:
    next_run_at = time.time() + interval_seconds
    print(
        f"runner: sleep={interval_seconds / 60:.1f}min next_run_at={local_time_text(next_run_at)}",
        file=sys.stderr,
        flush=True,
    )
    time.sleep(interval_seconds)


def main() -> int:
    args = build_parser().parse_args()
    raw_config = read_yaml_mapping(args.user_config)
    interval_minutes = resolve_interval_minutes(raw_config, args.interval_minutes)
    interval_seconds = interval_minutes * 60.0
    max_runs = resolve_max_runs(raw_config, args.once)
    run_on_start = resolve_run_on_start(raw_config, args.once)

    try:
        client = LorClient.from_files(args.user_config, args.connection_config)
        if args.print_proxy:
            print(f"connection: {client.connection.proxy_status()}", file=sys.stderr, flush=True)
        if args.login:
            client.login()
            print("login: ok")
            return 0
    except (LorError, ConnectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1

    print(
        f"runner: interval={interval_minutes:g}min min_interval={MIN_INTERVAL_MINUTES:g}min "
        f"max_runs={max_runs} run_on_start={str(run_on_start).lower()}",
        file=sys.stderr,
        flush=True,
    )

    run_count = 0
    if not run_on_start:
        sleep_between_runs(interval_seconds)

    while True:
        try:
            stats = client.run_once()
            print(json.dumps(stats_to_dict(stats), ensure_ascii=False), flush=True)
        except (LorError, ConnectionError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        except requests.HTTPError as exc:
            print(f"HTTP error: {exc}", file=sys.stderr, flush=True)
        except requests.RequestException as exc:
            print(f"Network error: {exc}", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            return 130

        run_count += 1
        if max_runs and run_count >= max_runs:
            return 0
        sleep_between_runs(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())