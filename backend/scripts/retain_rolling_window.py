"""Rolling-window retention for the Global Mosaic Tier A output directory --
follows THREDDS/
GMGSI's own file-based rolling-window pattern rather than a monolithic
ever-mutating Zarr cube, since Zarr has no cheap "append one, drop oldest"
operation.

Run this after each Tier A batch completes, against whatever directory
--final-out-dir already wrote to.

THREDDS's own catalog.xml (see services/thredds/catalog.xml) has
recheckEvery="10 min" on its aggregation, so it notices deletions on its
own periodic rescan -- this script does NOT need to (and does not) poke
THREDDS directly. Real gotcha to know about (verified against NcML docs):
an already-open aggregation handle can throw FileNotFoundException for a
file this script deletes mid-read, until THREDDS's next recheck notices
the removal -- an inherent property of the file-based design, not
something this script can fully prevent.

Same 7-day/672-file retention rule applies to the gallery preview images
(see run_phase2_tier.py's --gallery-dir) -- run this once per directory:
the main tiera_<ts>.nc dir, plus once per band's own gallery subdirectory
(gallery/tiera/{mir,wv,tir1,tir2}/), passing that band's name as --prefix
and .png as --suffix each time. Five invocations total for a full pass,
not one recursive call -- keeps each directory's retention independent
and the tool itself simple.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

def _filename_re(suffix: str) -> re.Pattern:
    """Build the regex matching this retention rule's `<prefix>_<timestamp>[_<timestamp>]<suffix>` filenames.

    Args:
        suffix: Filename suffix to match, e.g. ``".nc"`` or ``".png"``.

    Returns:
        Compiled regex with named groups ``prefix`` and ``ts`` (the
        first/target timestamp).
    """
    # The trailing (?:_\d{8}T\d{6})? handles gallery PNGs' dual-timestamp
    # name (<band>_<target>_<actual-mid-scan-time>.png, see
    # run_phase2_tier.py/generate_gallery_from_nc.py) -- retention always
    # keys off the FIRST (target) timestamp only, the real actual-time
    # second segment (if present) is purely informational and ignored
    # here. .nc files never have a second segment, so this is a no-op
    # for them.
    return re.compile(rf"^(?P<prefix>[a-zA-Z0-9]+)_(?P<ts>\d{{8}}T\d{{6}})(?:_\d{{8}}T\d{{6}})?{re.escape(suffix)}$")


def _parse_timestamp(path: Path, suffix: str) -> dt.datetime | None:
    """Extract the target timestamp from a retention-managed filename.

    Args:
        path: File path to parse (only its name is used).
        suffix: Expected filename suffix, e.g. ``".nc"`` or ``".png"``.

    Returns:
        The parsed target timestamp, or None if the filename doesn't
        match this suffix's naming convention.
    """
    match = _filename_re(suffix).match(path.name)
    if not match:
        return None
    try:
        return dt.datetime.strptime(match["ts"], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def find_expired(directory: Path, prefix: str, keep: int, suffix: str = ".nc") -> list[Path]:
    """Find files beyond the most recent `keep`, for retention deletion.

    Files beyond the most recent `keep`, oldest first. Only considers
    files matching `prefix`'s naming convention -- anything else in the
    directory (e.g. a stray file, a different prefix) is left untouched,
    not swept up by an overly broad glob. `suffix` generalizes this
    beyond the main tiera_<ts>.nc/tierb_<ts>.nc files -- gallery preview
    images use the same <prefix>_<timestamp> convention but with a
    per-band prefix (mir/wv/tir1/tir2) and a .png suffix instead (see
    run_phase2_tier.py's --gallery-dir); same retention rule applies to
    each band's own subdirectory, invoked separately per directory.

    Args:
        directory: Directory to scan for matching files.
        prefix: Filename prefix to match, e.g. ``"tiera"``.
        keep: Number of most-recent timestamps to keep.
        suffix: Filename suffix to match.

    Returns:
        Paths of files beyond the most recent `keep`, oldest first.
        Empty list if the directory has `keep` or fewer matching files.
    """
    candidates = []
    for path in directory.glob(f"{prefix}_*{suffix}"):
        ts = _parse_timestamp(path, suffix)
        if ts is not None:
            candidates.append((ts, path))

    candidates.sort(key=lambda pair: pair[0])  # oldest first
    if len(candidates) <= keep:
        return []
    return [path for _, path in candidates[: len(candidates) - keep]]


def main() -> None:
    """CLI entry point: parse args and delete (or dry-run report) expired retention files.

    Returns:
        None. Exits with status 1 if ``--dir`` is not a directory.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True, help="output directory to retain, e.g. output/tiera")
    parser.add_argument("--prefix", default="tiera", help="filename prefix to match, e.g. 'tiera' (default), 'tierb', or a band name ('mir'/'wv'/'tir1'/'tir2') for gallery dirs")
    parser.add_argument("--suffix", default=".nc", help="filename suffix to match, e.g. '.nc' (default) or '.png' for gallery preview images")
    parser.add_argument(
        "--keep",
        type=int,
        default=672,
        help="number of most-recent timestamps to keep. Default 672 = 4 timestamps/hour x 24h x 7 days "
        "(the rolling window this project settled on). "
        "Adjust if the real per-hour cadence or retention period ever changes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would be deleted without deleting it")
    args = parser.parse_args()

    if not args.dir.is_dir():
        print(f"ERROR: {args.dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    expired = find_expired(args.dir, args.prefix, args.keep, args.suffix)
    if not expired:
        print(f"Nothing to retire -- {args.prefix}_*{args.suffix} count is within the {args.keep}-file window.")
        return

    verb = "Would delete" if args.dry_run else "Deleting"
    for path in expired:
        print(f"{verb}: {path.name}")
        if not args.dry_run:
            path.unlink()

    print(f"\n{'Would retire' if args.dry_run else 'Retired'} {len(expired)} file(s), "
          f"{args.keep} most recent kept.")


if __name__ == "__main__":
    main()
