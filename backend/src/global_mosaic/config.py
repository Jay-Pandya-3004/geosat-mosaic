"""Phase 0's 6 logical mosaic slots and the satellite each currently resolves
to, using global_mosaic.satellites.coverage (date + longitude based, not a
hardcoded satellite id per slot -- see that module's docstring for why).

Replaces the earlier yaml-based SatelliteSlot config: sat_metadata.py is now
the single source of truth for satellite facts (positions, operational
windows), reader_registry.py for how to load their files, and this module
just defines which logical slots the mosaic covers.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from global_mosaic.satellites.coverage import Candidate, candidates_for_slot, resolve_slot

# Target longitude per logical slot -- see README's slot/coverage table.
# NOTE: the INSAT slot is 82.0, which OSCAR confirms is INSAT-3DS's position
# (not INSAT-3DR, which is at 74.0 -- an assumption corrected during the
# sat_metadata.py migration).
#
# NOTE (2026-07-30): meteosat_iodc's target of 41.5 is EUMETSAT's original
# design value for least projection distortion at the IODC service -- the
# satellite actually serving IODC today is Meteosat-9, currently at 45.5E
# (see sat_metadata.py's meteosat-09 entry). This slot used to resolve
# incorrectly to Meteosat-11 (the Rapid Scan Service backup for the Prime
# slot at 9.5E, a different EUMETSAT service, NOT IODC) because meteosat-09's
# Nominal_Longitude_Deg was stale at 0.0 with no Data_Available end date --
# 9.5E was closer to the 41.5 target than 0.0 was. Fixed by correcting
# meteosat-09's position in sat_metadata.py; coverage.py's proximity search
# now finds Meteosat-9 (offset 4.0 from target) ahead of Meteosat-11 (offset
# 32.0). Left target at 41.5 rather than changing it to 45.5, since 41.5 is
# EUMETSAT's own documented least-distortion value, not a wrong number.
PHASE0_SLOTS: dict[str, float] = {
    "goes_west": -137.0,
    "goes_east": -75.0,
    "meteosat_prime": 0.0,
    "meteosat_iodc": 41.5,
    "insat": 82.0,
    "himawari": 140.7,
}


@dataclass
class ResolvedSlot:
    """The satellite resolved to fill a Phase 0 logical slot at a given time.

    Attributes:
        slot_name: Logical slot name, a key of ``PHASE0_SLOTS``.
        satellite_key: Satellite key (matching
            ``global_mosaic.satellites.sat_metadata``) chosen to fill
            this slot.
        longitude_offset_deg: Absolute difference, in degrees, between
            the slot's target longitude and the resolved satellite's
            actual longitude.
    """

    slot_name: str
    satellite_key: str
    longitude_offset_deg: float


# INSAT-3DR and INSAT-3DS share the same 30-min full-disk cadence but run
# on opposite phases of it -- 3DS's real nominal filenames land on :00/:30,
# 3DR's on :15/:45 (confirmed from real sample files, see
# acquisition/insat.py's docstring). Real nominal-filename -> real-scan-mid-time
# offsets, duplicated from acquisition/insat.py's own
# _APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES rather than imported, to keep
# this module (pure metadata/arithmetic, no network) free of a dependency
# on the acquisition layer -- keep these two in sync if either is revised.
_INSAT_GENERATION_NOMINAL_PHASE_MINUTES = {
    "insat-3ds": 0.0,
    "insat-3dr": 15.0,
}
_INSAT_GENERATION_REAL_MID_OFFSET_MINUTES = {
    "insat-3ds": 14.029,
    "insat-3dr": 13.829,
}


def _nearest_insat_generation_real_mid(satellite_key: str, target_time: dt.datetime) -> dt.datetime:
    """Compute this INSAT generation's real scan mid-time nearest to `target_time`.

    This generation's real scan mid-time nearest to `target_time`, from
    pure nominal-cadence arithmetic (no network call) -- same model
    acquisition/insat.py uses to rank already-found candidates, just run
    in reverse to pick a generation before any search happens. Anchors
    the search grid on the REAL-mid phase (nominal phase + offset), not
    the nominal phase alone -- the offset (~14 min) is close enough to
    half the 30-min cycle that anchoring on nominal picks the wrong
    neighboring slot for targets near the middle of a cycle (caught via
    a real round-trip check against the four candidate timestamps below,
    which nominal-anchoring got completely wrong).

    Args:
        satellite_key: INSAT satellite key, must be a key of
            ``_INSAT_GENERATION_NOMINAL_PHASE_MINUTES`` (currently
            ``"insat-3ds"`` or ``"insat-3dr"``).
        target_time: Time to find the nearest real scan mid-time to (UTC).

    Returns:
        The estimated real scan mid-time (UTC) of this generation's
        nearest 30-minute-cadence cycle to `target_time`.
    """
    phase = dt.timedelta(minutes=_INSAT_GENERATION_NOMINAL_PHASE_MINUTES[satellite_key])
    offset = dt.timedelta(minutes=_INSAT_GENERATION_REAL_MID_OFFSET_MINUTES[satellite_key])
    cycle = dt.timedelta(minutes=30)
    epoch = dt.datetime(2000, 1, 1) + phase + offset  # anchored on this generation's REAL-mid phase
    nearest_n = round((target_time - epoch) / cycle)
    return epoch + nearest_n * cycle


def _resolve_insat_candidate(target_time: dt.datetime, candidates: list[Candidate]) -> Candidate | None:
    """Pick the INSAT-3DR/3DS candidate with the real scan mid-time nearest `target_time`.

    'Nearest INSAT generation wins' -- a feature designed but never
    implemented until now. Only
    tie-breaks between INSAT-3DR/3DS specifically (the only two
    generations with a live/current data path); any other operational
    INSAT candidate (e.g. INSAT-3D, or one of 3DR/3DS on a date where the
    other doesn't exist yet) is left to the caller's normal longitude-
    ranked fallback. Applies to the whole slot (one bundled file covers
    all 6 channels), not just WV -- WV is simply the one band where
    switching generations has a visible downstream consequence (3D/3DR
    are 8km-native, 3DS is 4km-native).

    Args:
        target_time: Desired scan mid-time (UTC).
        candidates: Longitude-ranked INSAT slot candidates, as returned
            by ``coverage.candidates_for_slot``.

    Returns:
        The INSAT-3DR/3DS candidate whose real scan mid-time is nearest
        `target_time`; if only one of them is present, that one; if
        neither is present, the single non-3DR/3DS candidate if there is
        exactly one, else None.
    """
    by_key = {c.satellite_key: c for c in candidates if c.satellite_key in _INSAT_GENERATION_REAL_MID_OFFSET_MINUTES}
    if len(by_key) <= 1:
        return next(iter(by_key.values()), None)
    gaps = {
        key: abs((_nearest_insat_generation_real_mid(key, target_time) - target_time).total_seconds())
        for key in by_key
    }
    return by_key[min(gaps, key=gaps.get)]


def resolve_phase0_slot(slot_name: str, target_time: dt.datetime) -> ResolvedSlot | None:
    """Resolve which satellite currently fills a Phase 0 slot at `target_time`.

    For the ``"insat"`` slot, applies the "nearest INSAT generation wins"
    tie-break (see :func:`_resolve_insat_candidate`) between INSAT-3DR
    and INSAT-3DS before falling back to the normal nearest-longitude
    candidate. All other slots resolve via a plain
    ``coverage.resolve_slot`` call.

    Args:
        slot_name: Logical slot name, a key of ``PHASE0_SLOTS``.
        target_time: Desired scan mid-time (UTC).

    Returns:
        The resolved :class:`ResolvedSlot`, or None if no satellite
        currently covers this slot at `target_time`.
    """
    target_lon = PHASE0_SLOTS[slot_name]
    if slot_name == "insat":
        ranked = candidates_for_slot(target_lon, target_time)
        insat_pick = _resolve_insat_candidate(target_time, ranked)
        if insat_pick is not None:
            return ResolvedSlot(slot_name, insat_pick.satellite_key, insat_pick.longitude_offset_deg)
        # Neither 3DR nor 3DS operational for target_time (e.g. a date
        # before 3DS existed and 3DR's own data start) -- fall through to
        # the normal nearest-longitude candidate (e.g. INSAT-3D).
        candidate = ranked[0] if ranked else None
        if candidate is None:
            return None
        return ResolvedSlot(slot_name, candidate.satellite_key, candidate.longitude_offset_deg)

    candidate: Candidate | None = resolve_slot(target_lon, target_time)
    if candidate is None:
        return None
    return ResolvedSlot(slot_name, candidate.satellite_key, candidate.longitude_offset_deg)


def resolve_all_phase0_slots(target_time: dt.datetime) -> dict[str, ResolvedSlot | None]:
    """Resolve every Phase 0 slot's satellite at `target_time`.

    Args:
        target_time: Desired scan mid-time (UTC).

    Returns:
        Mapping of slot name to its :class:`ResolvedSlot` (or None if
        unresolved), one entry per key of ``PHASE0_SLOTS``.
    """
    return {slot: resolve_phase0_slot(slot, target_time) for slot in PHASE0_SLOTS}
