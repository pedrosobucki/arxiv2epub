"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .ids import NotAnArxivReference
from .mathrender import MATH_FORMATS
from .pipeline import Options, build_epub
from .sources import NoHtmlAvailable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arxiv2epub",
        description="Turn an arXiv link into a well-formatted, Kindle-ready EPUB.",
        epilog="Example: arxiv2epub https://arxiv.org/abs/1706.03762 -o out/",
    )
    parser.add_argument(
        "reference",
        nargs="+",
        help="arXiv URL or id (e.g. 1706.03762, arXiv:2501.12948v1, an abs/pdf URL)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("out"),
        help="output file, or a directory to name the file automatically "
        "(default: ./out)",
    )
    parser.add_argument(
        "--math",
        choices=MATH_FORMATS,
        default="svg",
        dest="math_format",
        help="how to render equations: svg files (default), svg inlined into the "
        "text so it follows the reader's text colour, or png",
    )
    parser.add_argument(
        "--no-cover", action="store_true", help="skip the generated cover image"
    )
    parser.add_argument(
        "--no-images", action="store_true", help="skip downloading figures"
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        metavar="DIR",
        help="cache downloaded pages and figures in DIR between runs",
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="per-request timeout in seconds"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every step")
    parser.add_argument("-q", "--quiet", action="store_true", help="only report errors")
    parser.add_argument("--version", action="version", version=f"arxiv2epub {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.ERROR if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", stream=sys.stderr)

    options = Options(
        math_format=args.math_format,
        include_cover=not args.no_cover,
        download_images=not args.no_images,
        cache_dir=args.cache,
        timeout=args.timeout,
    )

    failures = 0
    for reference in args.reference:
        try:
            result = build_epub(reference, args.output, options)
        except NotAnArxivReference as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
        except (NoHtmlAvailable, LookupError) as exc:
            print(f"error: {reference}: {exc}", file=sys.stderr)
            failures += 1
        else:
            size = result.size_bytes / 1_048_576
            print(f"{result.path}  ({size:.1f} MiB)")
            for warning in result.warnings[:5]:
                print(f"  warning: {warning}", file=sys.stderr)
            extra = len(result.warnings) - 5
            if extra > 0:
                print(f"  ... and {extra} more warnings", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
