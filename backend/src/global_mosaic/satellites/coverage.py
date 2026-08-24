"""Resolves which physical satellite occupies a logical mosaic slot at a
given target time, using sat_metadata's Data_Available windows and each
satellite's Nominal_Longitude_Deg.

This is what makes the mosaic tolerant of satellite handovers (e.g.
GOES-16 -> GOES-19), relocations (e.g. the 2023 Meteosat-10/11 role swap
discovered while migrating this metadata), and outright satellite/portal
outages: callers get a ranked list of candidates, not one hardcoded
satellite, so a missing top choice just falls through to the next-nearest
(e.g. if INSAT's slot has no data, Meteosat-IODC or Himawari rank next by
longitude and get picked up automatically).

Replaces the earlier prototype's get_files_for_given_time.py, which only
classified time-coverage per satellite and never actually resolved a
winner.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from global_mosaic.satellites.sat_metadata import Satellites_Objects


@dataclass
class Candidate:
    """One satellite's eligibility/ranking for filling a mosaic slot.

    Attributes:
        satellite_key: Satellite key matching ``sat_metadata.py``.
        longitude_deg: The satellite's nominal longitude, in degrees.
        longitude_offset_deg: Angular offset between this satellite's
            longitude and the slot's target longitude, in [0, 180].
        status_rank: Preference rank derived from ``Satellite_Status``
            (0 = operational, higher = less preferred). Used only to
            break ties/near-ties in longitude offset.
    """

    satellite_key: str
    longitude_deg: float
    longitude_offset_deg: float
    status_rank: int = 0


def _status_rank(status: str | None) -> int:
    """Rank a satellite's free-text operational status for tie-breaking.

    Lower rank = preferred. Satellite_Status is free text, not a closed
    enum (e.g. "Standby", "Storage", "Operational (backup / Rapid Scan
    Service)", "Backup (sounder failed Sept 2020)"), so this substring-
    matches rather than looking up an exact value.

    Added after a real Phase 0 test run picked himawari-08 (Standby, no
    longer primary since 2025-11-26) over himawari-09 (Operational
    primary) for the Himawari slot -- both sit at the identical 140.7E
    longitude, so longitude_offset_deg alone couldn't break the tie, and
    dict iteration order (himawari-08 defined first in sat_metadata.py)
    silently won instead. himawari-08 then had no current data to find.

    Args:
        status: Free-text ``Satellite_Status`` value from sat_metadata.py,
            or None.

    Returns:
        0 for "operational"/"active", 1 for "standby", 2 for
        "storage"/"backup", 3 for anything else (including None/unknown).
    """
    if not status:
        return 3
    s = status.lower()
    if "operational" in s or s == "active":
        return 0
    if "standby" in s:
        return 1
    if "storage" in s or "backup" in s:
        return 2
    return 3  # inactive/decommissioned/deactivated/unrecognized


def _parse(iso: str | None) -> dt.datetime | None:
    """Parse an ISO-8601 string into a datetime, or None if empty/None.

    Args:
        iso: ISO-8601 datetime string, or None/empty.

    Returns:
        The parsed datetime, or None.
    """
    return dt.datetime.fromisoformat(iso) if iso else None


def _angular_offset(lon_a: float, lon_b: float) -> float:
    """Compute the shortest angular distance between two longitudes.

    Args:
        lon_a: First longitude, in degrees.
        lon_b: Second longitude, in degrees.

    Returns:
        The shortest angular distance between `lon_a` and `lon_b`, in
        [0, 180] degrees.
    """
    return abs(((lon_a - lon_b + 180) % 360) - 180)


def candidates_for_slot(
    target_lon_deg: float, target_time: dt.datetime, max_offset_deg: float = 90.0
) -> list[Candidate]:
    """List satellites operational at `target_time`, ranked nearest-longitude-first.

    `max_offset_deg` filters out satellites too far around the globe to be
    a sane fallback -- beyond that, limb distortion makes a substitution
    worse than leaving a gap.

    Args:
        target_lon_deg: Target slot longitude, in degrees.
        target_time: Desired scan time (UTC); a satellite must have
            ``Data_Available_Start_Date`` <= `target_time` (and no
            ``Date_Available_End_Date`` before it) to qualify.
        max_offset_deg: Maximum angular offset from `target_lon_deg` a
            candidate may have to be included.

    Returns:
        Candidates within `max_offset_deg`, sorted by
        ``(longitude_offset_deg, status_rank)`` ascending (nearest and
        most-operational first). Empty list if none qualify.
    """
    candidates = []
    for key, sat in Satellites_Objects.items():
        lon = sat.get("Nominal_Longitude_Deg")
        if lon is None:
            continue  # planned/no-data entries (e.g. himawari-10) have no position yet

        start = _parse(sat.get("Data_Available_Start_Date"))
        end = _parse(sat.get("Date_Available_End_Date"))
        if start is None or start > target_time:
            continue
        if end is not None and end < target_time:
            continue

        offset = _angular_offset(lon, target_lon_deg)
        if offset <= max_offset_deg:
            candidates.append(Candidate(key, lon, offset, _status_rank(sat.get("Satellite_Status"))))

    # Longitude offset remains the primary key (it's what actually
    # determines viewing-geometry quality for this slot) -- status only
    # breaks ties/near-ties, so it can't override a genuinely better-
    # positioned satellite, only decide between two that are (from this
    # static metadata's point of view) equally well positioned.
    candidates.sort(key=lambda c: (c.longitude_offset_deg, c.status_rank))
    return candidates


def resolve_slot(target_lon_deg: float, target_time: dt.datetime) -> Candidate | None:
    """Resolve the single best satellite for a slot at a given time.

    Args:
        target_lon_deg: Target slot longitude, in degrees.
        target_time: Desired scan time (UTC).

    Returns:
        The best-ranked :class:`Candidate` (see :func:`candidates_for_slot`),
        or None if nothing is operational within the fallback radius.
    """
    ranked = candidates_for_slot(target_lon_deg, target_time)
    return ranked[0] if ranked else None
