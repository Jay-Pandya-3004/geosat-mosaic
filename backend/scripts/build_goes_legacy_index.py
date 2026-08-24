#!/usr/bin/env python
"""Builds/refreshes the precomputed GOES-08..15 (legacy GridSat archive)
timestamp index at backend/data/cache/goes_sat_timestaps.json, by walking
NCEI's HTTP directory listing.

Migrated from the original prototype's generate_available_timestamp_geo_sats.py.
Fixed a bug there: it tried to resume from a nonexistent "config.json"
(there is no such file in this project -- almost certainly meant to open
the *output* file, goes_sat_timestaps.json, to resume/update it instead of
overwriting from scratch each run).

Only handles the legacy archive (goes-08..15). GOES-16..19 (ABI) don't need
an index -- acquisition/goes.py lists their AWS S3 buckets live, which is
cheap enough at 10-min cadence.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import requests

BASE_URL = "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/"
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "goes_sat_timestaps.json"


def get_available_years(url: str) -> list[str]:
    """List the year subdirectories available under an NCEI GridSat-GOES URL.

    Args:
        url: Base NCEI directory listing URL.

    Returns:
        Year strings (e.g. ``"2021"``) found as hrefs in the directory
        listing HTML.

    Raises:
        requests.HTTPError: If the request fails.
    """
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return re.findall(r'href="(\d{4})"', r.text)


def get_available_months(url: str, year: str) -> list[str]:
    """List the month subdirectories available under a given year.

    Args:
        url: Base NCEI directory listing URL.
        year: Year string, e.g. ``"2021"``.

    Returns:
        Two-digit month strings found as hrefs in the directory listing.

    Raises:
        requests.HTTPError: If the request fails.
    """
    r = requests.get(f"{url}{year}/", timeout=30)
    r.raise_for_status()
    return re.findall(r'href="(\d{2})"', r.text)


def get_available_files(url: str, year: str, month: str) -> list[str]:
    """List the GridSat-GOES NetCDF files available under a given year/month.

    Args:
        url: Base NCEI directory listing URL.
        year: Year string, e.g. ``"2021"``.
        month: Two-digit month string, e.g. ``"04"``.

    Returns:
        Filenames matching the GridSat-GOES naming convention found as
        hrefs in the directory listing.

    Raises:
        requests.HTTPError: If the request fails.
    """
    r = requests.get(f"{url}{year}/{month}/", timeout=30)
    r.raise_for_status()
    return re.findall(r'href="(GridSat-GOES\.goes\d{2}\.\d{4}\.\d{2}\.\d{2}\.\d{4}\.v\d+\.nc)"', r.text)


def build_index(resume: bool = True) -> dict:
    """Build (or resume/refresh) the GOES-08..15 legacy archive timestamp index.

    Walks NCEI's HTTP directory listing (years, then months, then files)
    and records each file's satellite id, ISO timestamp, filename, and
    download URL, then writes the result to ``INDEX_PATH``.

    Args:
        resume: If True (default) and ``INDEX_PATH`` already exists,
            load it first and update/extend it in place rather than
            starting from an empty index.

    Returns:
        The full index dict, keyed by legacy satellite id (e.g.
        ``"goes08"``) then by ISO timestamp string.
    """
    index: dict = {}
    if resume and INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    for year in get_available_years(BASE_URL):
        for month in get_available_months(BASE_URL, year):
            print(f"Searching files for: {year}-{month}")
            for file in get_available_files(BASE_URL, year, month):
                sat_id = re.search(r"goes\d{2}", file).group(0)
                gmt_str = re.search(r"\d{4}\.\d{2}\.\d{2}\.\d{4}", file).group(0)
                gmt = datetime.datetime.strptime(gmt_str, "%Y.%m.%d.%H%M")

                index.setdefault(sat_id, {})[gmt.isoformat()] = {
                    "satellite": sat_id,
                    "file_name": file,
                    "download_url": f"{BASE_URL}{year}/{month}/{file}",
                }

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=4), encoding="utf-8")
    return index


if __name__ == "__main__":
    build_index()
