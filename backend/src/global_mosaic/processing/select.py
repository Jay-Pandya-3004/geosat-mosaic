"""Per-pixel source selection across overlapping satellites.

Implements the original selection rule:
for pixels where more than one satellite has valid data, prefer the
satellite with the best time-sync to the target time; among satellites
that pass the sync bar, prefer the one with the finest ACTUAL resolution
at that pixel (processing/resolution.py's per-pixel value, which accounts
for view-zenith-angle degradation -- not the nominal nadir figure). A
strict sync threshold is optional (config-driven, per the original
"use a threshold if strict rule, else use the most accurate sync possible
without compromising spatial resolution" instruction).

Phase 0 simplification: one mid_time per satellite per run, not per
scanline/per-pixel -- so the sync check operates at the whole-satellite
level (a satellite either passes the sync bar for this run or it
doesn't), while the resolution comparison is genuinely per-pixel (real
resolution varies across a satellite's own disk, see resolution.py). A
later phase could refine sync to per-scanline granularity using each
reader's line-level timestamps (documented per satellite, e.g. SEVIRI's
L10LineMeanAcquisitionTime) if seam artefacts from this whole-scene
simplification turn out to matter in practice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import xarray as xr

SATELLITE_ID: dict[str, int] = {
    "goes-19": 1,
    "goes-18": 2,
    "meteosat-10": 3,
    "meteosat-09": 4,
    "insat-3ds": 5,
    "insat-3dr": 5,
    "insat-3d": 5,
    "himawari-09": 6,
    "himawari-08": 6,
}
"""Fixed integer keys for the mosaic's satellite_id mask + metadata table
(mosaic_io.py). Deliberately keyed by *physical satellite*, not logical
slot (config.py/coverage.py) -- the mosaic needs to record which bird
actually produced each pixel, and coverage.py's whole point is that the
occupant of a slot can change run to run. INSAT-3D/3DR/3DS share one ID
since only one of them will ever be a live candidate for the INSAT slot
at a given target_time; same reasoning doesn't apply to GOES-18/19 or the
two Himawaris/Meteosats, which are given distinct IDs since e.g. a
GOES-16 vs GOES-19 handover-period run could plausibly have historical
data from either satellite in the same mosaic."""


@dataclass
class Candidate:
    """One satellite's reprojected data for this mosaic run, ready for selection.

    Attributes:
        satellite_key: Satellite key, a key of ``SATELLITE_ID``.
        data: Reprojected values on the common grid; NaN = no data.
        resolution_km: Per-pixel ACTUAL resolution on the same grid
            (see processing/resolution.py); NaN = not visible.
        mid_time: This run's single scan mid-time for the whole scene.
        source_filename: Source file this candidate's data came from,
            for provenance/debugging.
        columns: See below.
    """

    satellite_key: str
    data: xr.DataArray  # reprojected values on the common grid; NaN = no data
    resolution_km: xr.DataArray  # per-pixel ACTUAL resolution on the same grid; NaN = not visible
    mid_time: dt.datetime  # this run's single scan mid-time for the whole scene
    source_filename: str = ""
    columns: np.ndarray | None = None
    """Full-grid column indices this candidate's data/resolution_km cover
    (see reproject.reproject_to_cropped_grid's crop_columns attribute) --
    None means data/resolution_km already span the FULL grid width (the
    original, uncropped contract every existing caller/test uses).
    Only meaningful to select_winners_streaming; select_winners itself
    still requires every candidate to share the same full shape and
    ignores this field entirely."""


@dataclass
class SelectionConfig:
    """Configuration for a single select_winners/select_winners_streaming run.

    Attributes:
        target_time: This mosaic run's target time, used to compute each
            candidate's sync gap.
        sync_threshold_seconds: See below.
        blend_margin_km: See below.
    """

    target_time: dt.datetime
    sync_threshold_seconds: float | None = None
    """None (default): best-effort mode -- every candidate stays eligible
    and resolution alone decides winners, so a satellite is never dropped
    on sync grounds even if a better-synced alternative exists elsewhere
    in this run; the "without compromising on spatial
    resolution" case. Set a number to enforce the strict rule instead:
    only candidates within `sync_threshold_seconds` of this run's best-
    synced candidate are eligible at all, even if that leaves some pixels
    with no coverage."""
    blend_margin_km: float = 0.0
    """Winners within this margin of the runner-up's actual resolution
    are flagged in the returned tie_mask for blend.py to feather instead
    of a hard cut. 0.0 (default) disables tie detection -- always a hard
    winner-take-all cut, per select.py's original planned design."""


@dataclass
class SelectionResult:
    """Per-pixel selection output: winning (and runner-up) source per pixel.

    Attributes:
        value: Winning pixel values.
        satellite_id: Integer satellite_id mask, 0 = no coverage.
        resolution_km: Winning candidate's actual resolution per pixel.
        tie_mask: Bool array, True where blend.py should feather instead
            of hard-cut.
        excluded_by_sync: Satellite_keys dropped by the sync threshold.
        runner_up_value: Second-best candidate's value; NaN where fewer
            than 2 candidates were valid.
        runner_up_resolution_km: Second-best candidate's actual
            resolution.
        runner_up_satellite_id: 0 where fewer than 2 candidates were
            valid.
    """

    value: xr.DataArray  # winning pixel values
    satellite_id: xr.DataArray  # integer satellite_id mask, 0 = no coverage
    resolution_km: xr.DataArray  # winning candidate's actual resolution per pixel
    tie_mask: xr.DataArray  # bool, True where blend.py should feather instead of hard-cut
    excluded_by_sync: list[str] = field(default_factory=list)  # satellite_keys dropped by the sync threshold
    runner_up_value: xr.DataArray | None = None  # second-best candidate's value; NaN where <2 candidates valid
    runner_up_resolution_km: xr.DataArray | None = None  # second-best candidate's actual resolution
    runner_up_satellite_id: xr.DataArray | None = None  # 0 where <2 candidates valid
    """The three runner_up_* fields are always populated whenever >=2
    candidates were passed in (regardless of blend_margin_km), so blend.py
    (or any other consumer) can feather/inspect the second-best source at
    any pixel, not only ones select.py itself flagged as a near-tie. They
    stay None only when select_winners was given a single candidate."""


def select_winners(candidates: list[Candidate], config: SelectionConfig) -> SelectionResult:
    """Select the per-pixel winning (and runner-up) satellite among candidates.

    Stacks every candidate's full-grid data/resolution arrays and picks,
    per pixel, the satellite with the finest actual resolution among
    those that pass the (optional) sync threshold. See this module's
    docstring for the full selection rule. For large grids where
    stacking every candidate is too memory-hungry, use
    :func:`select_winners_streaming` / :class:`SelectionAccumulator`
    instead -- same rule, processed one candidate at a time.

    Args:
        candidates: Candidates to select among; every candidate must
            share the same full grid shape.
        config: Selection configuration (target time, sync threshold,
            blend margin).

    Returns:
        The per-pixel :class:`SelectionResult`.

    Raises:
        ValueError: If `candidates` is empty, or `config.sync_threshold_seconds`
            excludes every candidate (e.g. a misconfigured negative
            threshold).
    """
    if not candidates:
        raise ValueError("select_winners called with no candidates")

    time_gaps = {
        c.satellite_key: abs((c.mid_time - config.target_time).total_seconds())
        for c in candidates
    }

    excluded_by_sync: list[str] = []
    if config.sync_threshold_seconds is not None:
        best_gap = min(time_gaps.values())
        surviving = []
        for c in candidates:
            if time_gaps[c.satellite_key] - best_gap <= config.sync_threshold_seconds:
                surviving.append(c)
            else:
                excluded_by_sync.append(c.satellite_key)
    else:
        surviving = list(candidates)

    if not surviving:
        # Shouldn't happen -- the candidate with best_gap always has a
        # gap-to-best of exactly 0, which always survives its own
        # threshold check -- but guard against a caller passing a
        # negative threshold or similar misuse rather than fail silently.
        raise ValueError(
            "sync_threshold_seconds excluded every candidate; check for a "
            "misconfigured (e.g. negative) threshold."
        )

    grid_like = surviving[0].data
    data_stack = np.stack([c.data.values for c in surviving], axis=0)  # (n, y, x)
    res_stack = np.stack([c.resolution_km.values for c in surviving], axis=0)  # (n, y, x)

    valid = ~np.isnan(data_stack) & ~np.isnan(res_stack)
    res_for_argmin = np.where(valid, res_stack, np.inf)

    # Full argsort (not just argmin) since we need both the winner (rank 0)
    # and the runner-up (rank 1) -- cheap regardless, n candidates is at
    # most 6 here.
    sorted_idx = np.argsort(res_for_argmin, axis=0)  # (n, y, x), ascending actual resolution
    winner_idx = sorted_idx[0]
    any_valid = valid.any(axis=0)  # (y, x): does *any* candidate cover this pixel?
    n_valid = valid.sum(axis=0)  # (y, x): how many candidates cover this pixel?

    ids = np.array([SATELLITE_ID[c.satellite_key] for c in surviving])
    winner_satellite_id = np.where(any_valid, ids[winner_idx], 0).astype(np.int16)

    y_idx, x_idx = np.indices(winner_idx.shape)
    winner_value = np.where(any_valid, data_stack[winner_idx, y_idx, x_idx], np.nan)
    winner_resolution = np.where(any_valid, res_stack[winner_idx, y_idx, x_idx], np.nan)

    runner_up_value_arr = runner_up_resolution_arr = runner_up_id_arr = None
    tie_mask = np.zeros(winner_idx.shape, dtype=bool)
    if len(surviving) > 1:
        runner_idx = sorted_idx[1]
        has_runner_up = n_valid >= 2

        runner_up_value_arr = np.where(has_runner_up, data_stack[runner_idx, y_idx, x_idx], np.nan)
        runner_up_resolution_arr = np.where(has_runner_up, res_stack[runner_idx, y_idx, x_idx], np.nan)
        runner_up_id_arr = np.where(has_runner_up, ids[runner_idx], 0).astype(np.int16)

        if config.blend_margin_km > 0:
            tie_mask = has_runner_up & ((runner_up_resolution_arr - winner_resolution) <= config.blend_margin_km)

    coords, dims = grid_like.coords, grid_like.dims
    return SelectionResult(
        value=xr.DataArray(winner_value, coords=coords, dims=dims),
        satellite_id=xr.DataArray(winner_satellite_id, coords=coords, dims=dims),
        resolution_km=xr.DataArray(winner_resolution, coords=coords, dims=dims),
        tie_mask=xr.DataArray(tie_mask, coords=coords, dims=dims),
        excluded_by_sync=excluded_by_sync,
        runner_up_value=None if runner_up_value_arr is None else xr.DataArray(runner_up_value_arr, coords=coords, dims=dims),
        runner_up_resolution_km=None if runner_up_resolution_arr is None else xr.DataArray(runner_up_resolution_arr, coords=coords, dims=dims),
        runner_up_satellite_id=None if runner_up_id_arr is None else xr.DataArray(runner_up_id_arr, coords=coords, dims=dims),
    )


def compute_sync_exclusion(
    satellite_mid_times: dict[str, dt.datetime],
    target_time: dt.datetime,
    sync_threshold_seconds: float | None,
) -> tuple[set[str], list[str]]:
    """Decide which satellites survive the sync threshold, from cheap metadata alone.

    The sync-threshold decision, factored out so a caller doing TRUE
    one-candidate-at-a-time streaming (see SelectionAccumulator) can make
    this call from cheap metadata alone (satellite_key -> mid_time) --
    already known before a candidate's expensive data is ever built (see
    run_phase2_tier.py: real scan mid-time comes from opening the raw
    file, well before reprojection) -- and skip building a sync-excluded
    candidate's data at all, instead of building it and then discarding
    it.

    Args:
        satellite_mid_times: Mapping of satellite_key to this run's scan
            mid-time for that satellite.
        target_time: This mosaic run's target time.
        sync_threshold_seconds: None for best-effort mode (every
            satellite survives); a number to enforce the strict rule
            (only satellites within this many seconds of the best-synced
            one survive).

    Returns:
        Tuple of ``(surviving_keys, excluded_keys)`` -- `surviving_keys`
        is a set of satellite_keys, `excluded_keys` a list.
    """
    if sync_threshold_seconds is None:
        return set(satellite_mid_times), []
    time_gaps = {k: abs((t - target_time).total_seconds()) for k, t in satellite_mid_times.items()}
    best_gap = min(time_gaps.values())
    surviving = {k for k, gap in time_gaps.items() if gap - best_gap <= sync_threshold_seconds}
    excluded = [k for k in satellite_mid_times if k not in surviving]
    return surviving, excluded


def _as_slice_if_contiguous(cols: np.ndarray) -> slice | np.ndarray:
    """Convert a contiguous ascending integer column-index array into a cheap slice.

    Real profiling found SelectionAccumulator.update()'s
    outer `array[:, cols]` was the single largest cost in the whole
    pipeline -- up to 48% of total time on Tier B's grid -- because
    `cols` was always a plain integer array, and numpy's fancy indexing
    always copies on read and scatters on write, even when the indices
    are just a contiguous run. Only 2 of 6 satellites (GOES-West,
    Himawari, see reproject.visible_columns_for_satellite's antimeridian
    note) actually wrap and need real fancy indexing; the other 4 are a
    plain ascending range every time. A real Python `slice` there is a
    VIEW, not a copy -- numpy handles `array[:, slice]` as cheap pointer/
    stride arithmetic instead of gathering/scattering hundreds of
    millions of elements.

    Args:
        cols: Column indices (typically from ``Candidate.columns``).

    Returns:
        A `slice(start, start+len)` if `cols` is contiguous ascending
        integers, else `cols` unchanged. An empty `cols` returns
        `slice(0, 0)`.
    """
    if len(cols) == 0:
        return slice(0, 0)
    start = int(cols[0])
    if np.array_equal(cols, np.arange(start, start + len(cols))):
        return slice(start, start + len(cols))
    return cols


class SelectionAccumulator:
    """The actual streaming "top-2 by resolution" reduction (see
    select_winners_streaming's docstring for the full rationale/proof
    sketch) exposed as its own object with an `update()` method, so a
    caller can fold one candidate in and let it go out of scope
    (discarding its raw data) before building the next one -- the
    difference between this and select_winners_streaming's list-based
    convenience wrapper is NOT the per-pixel math (identical), it's
    *when* each candidate's memory can be released. Confirmed necessary
    against real data: even with select_winners_streaming
    avoiding the (n_candidates, H, W) stack internally, a caller that
    still builds every candidate into a list before calling it keeps all
    of them alive simultaneously anyway -- on Tier B's 1km grid with all
    6 real satellites, that alone still produced a real MemoryError at
    the final selection step. This class is what run_phase2_tier.py's
    driver actually uses; select_winners_streaming stays as a simpler
    entry point for callers (and this module's own equivalence tests)
    that already have every candidate built.
    """

    _FLOAT32_FIELDS = 4  # best_value, best_res, runner_value, runner_res
    _INT16_FIELDS = 3  # best_id, runner_id, n_valid_count

    @classmethod
    def bytes_per_row(cls, width: int) -> int:
        """Compute this class's memory cost per grid row.

        Memory cost of one grid row across all 7 arrays this class
        allocates -- used by processing/tiling.py to size tiles from a
        memory budget without hardcoding this class's dtype layout
        separately (and silently drifting out of sync if that layout ever
        changes). Per tracemalloc, at Tier B width
        (40075), this formula gives ~0.86 MB/row, matching a measured
        12.16GB total accumulator at full 14806-row height.

        Args:
            width: Grid width, in pixels.

        Returns:
            Bytes needed for one row across all 7 accumulator arrays.
        """
        return width * (cls._FLOAT32_FIELDS * 4 + cls._INT16_FIELDS * 2)

    def __init__(self, grid_shape: tuple[int, int]):
        """Allocate the full-grid best/runner-up accumulator arrays.

        Args:
            grid_shape: ``(height, width)`` of the full target grid.
        """
        height, width = grid_shape
        self.width = width
        self.best_value = np.full((height, width), np.nan, dtype=np.float32)
        self.best_res = np.full((height, width), np.inf, dtype=np.float32)
        self.best_id = np.zeros((height, width), dtype=np.int16)
        self.runner_value = np.full((height, width), np.nan, dtype=np.float32)
        self.runner_res = np.full((height, width), np.inf, dtype=np.float32)
        self.runner_id = np.zeros((height, width), dtype=np.int16)
        self.n_valid_count = np.zeros((height, width), dtype=np.int16)
        self.n_candidates_folded = 0

    def update(self, c: Candidate) -> None:
        """Fold one candidate into the running best/runner-up state.

        Compares `c`'s valid pixels (within its own columns, if
        cropped) against the current best/runner-up resolution and
        updates in place -- demoting the current best to runner-up
        wherever `c` displaces it. `c` can then go out of scope and be
        garbage-collected; this accumulator retains no reference to it.

        Args:
            c: The candidate to fold in.

        Returns:
            None. Mutates this accumulator's internal state in place.
        """
        cols = _as_slice_if_contiguous(c.columns) if c.columns is not None else slice(0, self.width)
        c_value = np.asarray(c.data.values, dtype=np.float32)
        c_res = np.asarray(c.resolution_km.values, dtype=np.float32)
        valid = ~np.isnan(c_value) & ~np.isnan(c_res)
        c_id = SATELLITE_ID[c.satellite_key]

        bv, br, bid = self.best_value[:, cols], self.best_res[:, cols], self.best_id[:, cols]
        rv, rr, rid = self.runner_value[:, cols], self.runner_res[:, cols], self.runner_id[:, cols]

        becomes_best = valid & (c_res < br)
        becomes_runner = valid & ~becomes_best & (c_res < rr)

        # Demote the current best to runner-up wherever this candidate
        # displaces it -- must happen before overwriting best below.
        rv[becomes_best] = bv[becomes_best]
        rr[becomes_best] = br[becomes_best]
        rid[becomes_best] = bid[becomes_best]

        bv[becomes_best] = c_value[becomes_best]
        br[becomes_best] = c_res[becomes_best]
        bid[becomes_best] = c_id

        rv[becomes_runner] = c_value[becomes_runner]
        rr[becomes_runner] = c_res[becomes_runner]
        rid[becomes_runner] = c_id

        # When cols is a slice (the common case, see
        # _as_slice_if_contiguous), bv/br/bid/rv/rr/rid are VIEWS into
        # self.*, so the in-place updates above already landed -- this
        # write-back is then a cheap no-op self-assignment, kept anyway
        # so the same code path is correct regardless of whether cols
        # ended up a slice or a genuine fancy-index array (the wrapped
        # case, where bv etc. really are copies and this write-back is
        # the only thing that makes the update visible).
        self.best_value[:, cols], self.best_res[:, cols], self.best_id[:, cols] = bv, br, bid
        self.runner_value[:, cols], self.runner_res[:, cols], self.runner_id[:, cols] = rv, rr, rid
        self.n_valid_count[:, cols] += valid.astype(np.int16)
        self.n_candidates_folded += 1

    def finalize(
        self,
        excluded_by_sync: list[str],
        blend_margin_km: float,
        full_coords: dict | None = None,
        full_dims: tuple[str, str] = ("y", "x"),
    ) -> SelectionResult:
        """Finalize the accumulated best/runner-up state into a SelectionResult.

        Consumes this accumulator -- do not call update() again or
        reuse it after finalize() returns. Mutates self.best_value/
        best_res/runner_value/runner_res/runner_id in place and hands
        them straight back as the SelectionResult (no copy), rather than
        the array-copying np.where() approach this used to take (see
        below for why) -- so this accumulator's internal state is no
        longer meaningful afterward.

        In-place, not np.where(): np.where always allocates a brand-new
        full-grid array, and this method used to call it 5 times plus
        one .astype() (another copy) -- on Tier B's 593M-pixel grid
        that's 10+ GB of fresh allocations on top of the ~13GB the
        accumulator's own 7 arrays already hold, and was a real,
        reproduced MemoryError (finalize() itself, not
        update() -- update()'s cost was already fixed separately, see
        _as_slice_if_contiguous).

        Args:
            excluded_by_sync: Satellite_keys already excluded by the
                sync threshold (before any candidate was folded in),
                passed straight through to the result.
            blend_margin_km: Tie-detection margin; 0.0 disables it.
            full_coords: Coordinates for the returned DataArrays,
                describing the full target grid.
            full_dims: Dimension names for the returned DataArrays.

        Returns:
            The finalized :class:`SelectionResult`, spanning the full
            grid described by `full_coords`/`full_dims`.
        """
        any_valid = self.best_id != 0
        invalid = ~any_valid
        self.best_value[invalid] = np.nan
        self.best_res[invalid] = np.nan
        winner_value = self.best_value
        winner_resolution = self.best_res
        winner_satellite_id = self.best_id

        has_runner_up = self.n_valid_count >= 2
        tie_mask = np.zeros(self.best_id.shape, dtype=bool)

        runner_up_value_arr = runner_up_resolution_arr = runner_up_id_arr = None
        if self.n_candidates_folded > 1:
            not_runner_up = ~has_runner_up
            self.runner_value[not_runner_up] = np.nan
            self.runner_res[not_runner_up] = np.nan
            self.runner_id[not_runner_up] = 0
            runner_up_value_arr = self.runner_value
            runner_up_resolution_arr = self.runner_res
            runner_up_id_arr = self.runner_id  # already int16 -- no .astype() copy needed

            if blend_margin_km > 0:
                tie_mask = has_runner_up & ((runner_up_resolution_arr - winner_resolution) <= blend_margin_km)

        dims = full_dims if full_coords is not None else ("y", "x")
        coords = full_coords if full_coords is not None else {}

        return SelectionResult(
            value=xr.DataArray(winner_value, coords=coords, dims=dims),
            satellite_id=xr.DataArray(winner_satellite_id, coords=coords, dims=dims),
            resolution_km=xr.DataArray(winner_resolution, coords=coords, dims=dims),
            tie_mask=xr.DataArray(tie_mask, coords=coords, dims=dims),
            excluded_by_sync=excluded_by_sync,
            runner_up_value=None if runner_up_value_arr is None else xr.DataArray(runner_up_value_arr, coords=coords, dims=dims),
            runner_up_resolution_km=None if runner_up_resolution_arr is None else xr.DataArray(runner_up_resolution_arr, coords=coords, dims=dims),
            runner_up_satellite_id=None if runner_up_id_arr is None else xr.DataArray(runner_up_id_arr, coords=coords, dims=dims),
        )


def select_winners_streaming(
    candidates: list[Candidate],
    config: SelectionConfig,
    grid_shape: tuple[int, int],
    full_coords: dict | None = None,
    full_dims: tuple[str, str] = ("y", "x"),
) -> SelectionResult:
    """Select per-pixel winners the same way as select_winners, one candidate at a time.

    Same selection rule and SelectionResult contract as select_winners
    (see that function's tests -- test_select_streaming_equivalence.py
    proves this produces IDENTICAL output on every scenario select_winners
    itself is tested against), but processes candidates ONE AT A TIME
    instead of stacking all of them into one (n_candidates, H, W) array.

    Why this exists: select_winners' np.stack of every candidate's full
    array is exactly what caused real, reproduced MemoryErrors
    once the grid got large enough (Tier B's 1km/593M-pixel
    grid) -- doubling from n=1 to n=6 candidates doubles-then-sextuples
    that stack's size. This function instead keeps only a running
    "best so far" and "runner-up so far" pair of full-grid arrays (6
    arrays total: value/resolution/id x2) -- independent of how many
    candidates are processed, because each new candidate is compared
    against the running state and then discarded, never stacked with the
    others. This is the standard "streaming top-2" reduction and is
    provably equivalent to sort-and-take-top-2 for any processing order,
    given resolution values that aren't exact bitwise ties across
    satellites (true in practice -- real per-pixel resolution is a
    continuous function of view geometry, essentially never identical
    between two different satellites; synthetic test fixtures with exact
    ties should not rely on this function matching select_winners' exact
    stable-sort tie-break, since the two algorithms may break an exact
    tie differently).

    Also accepts candidates whose data/resolution_km only cover a
    *cropped* subset of the full grid's columns (Candidate.columns, see
    reproject.reproject_to_cropped_grid) -- each candidate only ever
    needs to touch its own columns of the running state, which is what
    makes combining this with footprint-cropped candidates keep both
    compute AND memory bounded by roughly one satellite's footprint
    rather than the full grid, unlike select_winners which requires
    every candidate to already share the exact same full shape.

    `grid_shape`/`full_coords`/`full_dims` describe the FULL target grid
    the returned SelectionResult should span -- can't be inferred from
    the candidates themselves since a fully-cropped run's candidates
    might never individually cover the whole grid.

    Args:
        candidates: Candidates to select among; may be cropped to a
            subset of grid columns via ``Candidate.columns``.
        config: Selection configuration (target time, sync threshold,
            blend margin).
        grid_shape: ``(height, width)`` of the full target grid.
        full_coords: Coordinates for the returned DataArrays, describing
            the full target grid.
        full_dims: Dimension names for the returned DataArrays.

    Returns:
        The per-pixel :class:`SelectionResult`, spanning the full grid.

    Raises:
        ValueError: If `candidates` is empty, or the sync threshold
            excludes every candidate.
    """
    candidates = list(candidates)
    if not candidates:
        raise ValueError("select_winners_streaming called with no candidates")

    mid_times = {c.satellite_key: c.mid_time for c in candidates}
    surviving_keys, excluded_by_sync = compute_sync_exclusion(
        mid_times, config.target_time, config.sync_threshold_seconds
    )
    surviving = [c for c in candidates if c.satellite_key in surviving_keys]

    if not surviving:
        raise ValueError(
            "sync_threshold_seconds excluded every candidate; check for a "
            "misconfigured (e.g. negative) threshold."
        )

    accumulator = SelectionAccumulator(grid_shape)
    for c in surviving:
        accumulator.update(c)

    return accumulator.finalize(excluded_by_sync, config.blend_margin_km, full_coords, full_dims)
