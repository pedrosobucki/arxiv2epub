"""Entry point for the mail-in worker."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .. import __version__
from .config import ConfigError, load_config
from .worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arxiv2epub-worker",
        description="Watch a mailbox for arXiv links, convert them, and mail "
        "the EPUB to a Kindle.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="load settings from a .env file before reading the environment "
        "(not needed under Docker Compose, which supplies them directly)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process the inbox a single time and exit, instead of polling",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the configuration and exit without touching the network",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every step")
    parser.add_argument(
        "--version", action="version", version=f"arxiv2epub-worker {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    try:
        config = load_config(args.env_file)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(config.describe())
        if config.ignored_keys:
            print(f"ignored (unused by this version): {', '.join(config.ignored_keys)}")
        return 0

    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.cache_dir:
        config.cache_dir.mkdir(parents=True, exist_ok=True)

    worker = Worker(config)
    if args.once:
        worker.poll()
    else:
        worker.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
