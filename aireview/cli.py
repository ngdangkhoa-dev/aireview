"""Command-line interface for AIReview."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import GitError, build_review, render_markdown, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aireview",
        description=(
            "Plain-language review of what your AI pair-programmer just "
            "changed in this git repo — with risk flags."
        ),
    )
    parser.add_argument(
        "--since",
        metavar="REF",
        default=None,
        help="Compare REF (branch / tag / commit) to the working tree "
             "instead of comparing HEAD to the working tree.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default="aireview-report.md",
        help="Where to write the Markdown report (default: aireview-report.md).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the summary only; do not write the report file.",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    try:
        review = build_review(cwd, since=args.since)
    except GitError as exc:
        print(f"aireview: {exc}", file=sys.stderr)
        return 2

    print(render_text(review))
    if not args.no_write:
        out = Path(args.output)
        out.write_text(render_markdown(review), encoding="utf-8")
        print(f"\nĐã ghi report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
