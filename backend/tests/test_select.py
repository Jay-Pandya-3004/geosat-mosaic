"""Unit tests for processing/select.py's per-pixel selection rule:
sync threshold, then
finest actual resolution, with tie detection for blend.py. All synthetic."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from global_mosaic.processing.select import Candidate, SelectionConfig, select_winners

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _grid_da(values):
    coords = {"y": np.arange(3), "x": np.arange(3)}
    return xr.DataArray(np.array(values, dtype=float), coords=coords, dims=["y", "x"])


@pytest.fixture
def two_candidates():
    """A (coarse, well-synced, full coverage) vs B (fine, poorly-synced,
    top-row-only coverage) -- mirrors the manual check done during
    development."""
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    b_data = _grid_da([[20, 20, 20], [np.nan] * 3, [np.nan] * 3])
    b_res = _grid_da([[1.0, 1.0, 1.0], [np.nan] * 3, [np.nan] * 3])
    return [
        Candidate("goes-19", a_data, a_res, TARGET),
        Candidate("himawari-09", b_data, b_res, TARGET + dt.timedelta(seconds=600)),
    ]


def test_best_effort_mode_finest_resolution_wins_where_available(two_candidates):
    result = select_winners(two_candidates, SelectionConfig(target_time=TARGET))
    # Top row: both cover it, B (1km) beats A (4km).
    assert (result.satellite_id.values[0] == 6).all()
    assert (result.value.values[0] == 20.0).all()
    # Bottom rows: only A covers it.
    assert (result.satellite_id.values[1:] == 1).all()
    assert (result.value.values[1:] == 10.0).all()
    assert result.excluded_by_sync == []


def test_strict_sync_threshold_excludes_worse_synced_candidate(two_candidates):
    result = select_winners(
        two_candidates, SelectionConfig(target_time=TARGET, sync_threshold_seconds=60)
    )
    assert result.excluded_by_sync == ["himawari-09"]
    assert (result.satellite_id.values == 1).all()


def test_tie_detection_flags_close_resolutions(two_candidates):
    # diff = 4.0 - 1.0 = 3.0, within a 3.5km margin -> tie in the top row only.
    result = select_winners(
        two_candidates, SelectionConfig(target_time=TARGET, blend_margin_km=3.5)
    )
    assert (result.tie_mask.values[0] == True).all()  # noqa: E712
    assert (result.tie_mask.values[1:] == False).all()  # noqa: E712


def test_no_tie_when_margin_too_small(two_candidates):
    result = select_winners(
        two_candidates, SelectionConfig(target_time=TARGET, blend_margin_km=1.0)
    )
    assert not result.tie_mask.values.any()


def test_runner_up_fields_populated_with_multiple_candidates(two_candidates):
    result = select_winners(two_candidates, SelectionConfig(target_time=TARGET))
    assert result.runner_up_value is not None
    # Top row: runner-up to B (winner) is A.
    assert (result.runner_up_satellite_id.values[0] == 1).all()
    assert (result.runner_up_value.values[0] == 10.0).all()
    # Bottom rows: only one candidate ever covers them -> no runner-up.
    assert (result.runner_up_satellite_id.values[1:] == 0).all()
    assert np.isnan(result.runner_up_value.values[1:]).all()


def test_single_candidate_has_no_runner_up(two_candidates):
    result = select_winners([two_candidates[0]], SelectionConfig(target_time=TARGET))
    assert result.runner_up_value is None
    assert result.runner_up_satellite_id is None
    assert result.runner_up_resolution_km is None
    assert not result.tie_mask.values.any()


def test_no_candidates_raises():
    with pytest.raises(ValueError):
        select_winners([], SelectionConfig(target_time=TARGET))


def test_no_coverage_pixel_gets_id_zero():
    empty = _grid_da(np.full((3, 3), np.nan))
    empty_res = _grid_da(np.full((3, 3), np.nan))
    partial_data = _grid_da([[1, 1, 1], [np.nan] * 3, [np.nan] * 3])
    partial_res = _grid_da([[2.0, 2.0, 2.0], [np.nan] * 3, [np.nan] * 3])
    candidates = [Candidate("goes-19", partial_data, partial_res, TARGET)]
    result = select_winners(candidates, SelectionConfig(target_time=TARGET))
    assert (result.satellite_id.values[1:] == 0).all()
    assert np.isnan(result.value.values[1:]).all()
