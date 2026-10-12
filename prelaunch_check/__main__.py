"""Command line: python -m prelaunch_check CONFIG.json [--json] [--fail-on-warn]"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .checks import Result, run_all
from .client import Fetcher
from .config import ConfigError, load

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2


def summarize(results: List[Result]) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0}
    for r in results:
        counts[r.level] = counts.get(r.level, 0) + 1
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prelaunch-check",
        description="Read-only checks to run on a website before opening it to the public.",
    )
    parser.add_argument("config", help="path to the JSON configuration file")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument("--fail-on-warn", action="store_true", help="exit with code 1 on warnings too")
    parser.add_argument("--version", action="version", version=f"prelaunch-check {__version__}")
    args = parser.parse_args(argv)

    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    results = run_all(cfg, Fetcher(timeout=cfg.timeout))
    counts = summarize(results)

    if args.json:
        print(json.dumps({"site": cfg.origin, "summary": counts, "results": [r.__dict__ for r in results]}, indent=2))
    else:
        print(f"prelaunch-check {__version__} · {cfg.origin} · {'indexable' if cfg.expect_indexable else 'closed to search engines'}\n")
        for r in results:
            line = f"  {r.level:<4}  {r.check:<15} {r.subject}"
            print(line + (f"  -> {r.detail}" if r.detail else ""))
        print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['WARN']} warnings, {counts['INFO']} notes.")

    if counts["FAIL"] or (args.fail_on_warn and counts["WARN"]):
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
