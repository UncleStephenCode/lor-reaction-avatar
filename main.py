#!/usr/bin/env python3
"""Runner for LOR reaction-rate avatar updates."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from libs.connection import ConnectionError
from libs.lor_client import LorClient, LorError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LOR reaction-rate avatar updater")
    parser.add_argument("--user-config", default="configs/user.yml", help="Path to configs/user.yml")
    parser.add_argument("--connection-config", default="configs/conn.yml", help="Path to configs/conn.yml")
    parser.add_argument("--once", action="store_true", help="Run one scan/update iteration and exit")
    parser.add_argument("--login", action="store_true", help="Only log in and save cookies")
    parser.add_argument("--print-proxy", action="store_true", help="Print connection proxy status")
    return parser


def stats_to_dict(stats) -> dict[str, object]:
    return {
        "counts": stats.counts,
        "rates": stats.rates,
        "avatar_path": str(stats.avatar_path) if stats.avatar_path else "",
        "uploaded": stats.uploaded,
        "changed": stats.changed,
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = LorClient.from_files(args.user_config, args.connection_config)
        if args.print_proxy:
            print(f"connection: {client.connection.proxy_status()}", file=sys.stderr)
        if args.login:
            client.login()
            print("login: ok")
            return 0

        runner = client.config.runner
        interval = 3600.0 / max(1, runner.runs_per_hour)
        max_runs = 1 if args.once else runner.max_runs
        run_count = 0

        if not runner.run_on_start and not args.once:
            time.sleep(interval)

        while True:
            stats = client.run_once()
            print(json.dumps(stats_to_dict(stats), ensure_ascii=False), flush=True)
            run_count += 1
            if args.once or (max_runs and run_count >= max_runs):
                return 0
            time.sleep(interval)
    except (LorError, ConnectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
