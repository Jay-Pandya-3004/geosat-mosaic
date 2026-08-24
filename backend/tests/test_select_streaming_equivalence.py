"""Proves select_winners_streaming produces IDENTICAL SelectionResult
output to the existing, well-tested select_winners on every scenario
test_select.py covers -- required before anything switches over to using
the streaming version (see select.py's own docstring on why: it exists
purely to avoid select_winners' O(n_candidates) full-grid stack, which
caused real, reproduced MemoryErrors on Tier B's 1km grid).
Also covers footprint-cropped candidates (select_winners itself can't
even accept those -- it requires uniform shape), the actual scenario this
function was built for."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from global_mosaic.processing.select import (
    Candidate,
    _as_slice_if_contiguous,
    SelectionAccumulator,
    SelectionConfig,
    compute_sync_exclusion,
    select_winners,
    select_winners_streaming,
)

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _grid_da(values):
    coords = {"y": np.arange(3), "x": np.arange(3)}
    return xr.DataArray(np.array(values, dtype=float), coords=coords, dims=["y", "x"])


def _assert_results_equal(a, b):
    assert np.array_equal(a.value.values, b.value.values, equal_nan=True)
    assert np.array_equal(a.satellite_id.values, b.satellite_id.values)
    assert np.array_equal(a.resolution_km.values, b.resolution_km.values, equal_nan=True)
    assert np.array_equal(a.tie_mask.values, b.tie_mask.values)
    assert sorted(a.excluded_by_sync) == sorted(b.excluded_by_sync)
    if a.runner_up_value is None:
        assert b.runner_up_value is None
    else:
        assert np.array_equal(a.runner_up_value.values, b.runner_up_value.values, equal_nan=True)
        assert np.array_equal(a.runner_up_satellite_id.values, b.runner_up_satellite_id.values)
        assert np.array_equal(a.runner_up_resolution_km.values, b.runner_up_resolution_km.values, equal_nan=True)


@pytest.fixture
def two_candidates():
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    b_data = _grid_da([[20, 20, 20], [np.nan] * 3, [np.nan] * 3])
    b_res = _grid_da([[1.0, 1.0, 1.0], [np.nan] * 3, [np.nan] * 3])
    return [
        Candidate("goes-19", a_data, a_res, TARGET),
        Candidate("himawari-09", b_data, b_res, TARGET + dt.timedelta(seconds=600)),
    ]


@pytest.mark.parametrize(
    "config",
    [
        SelectionConfig(target_time=TARGET),
        SelectionConfig(target_time=TARGET, sync_threshold_seconds=60),
        SelectionConfig(target_time=TARGET, blend_margin_km=3.5),
        SelectionConfig(target_time=TARGET, blend_margin_km=1.0),
    ],
)
def test_streaming_matches_stack_based_two_candidates(two_candidates, config):
    stack_result = select_winners(two_candidates, config)
    streaming_result = select_winners_streaming(two_candidates, config, grid_shape=(3, 3))
    _assert_results_equal(stack_result, streaming_result)


def test_streaming_matches_stack_based_single_candidate():
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    candidates = [Candidate("goes-19", a_data, a_res, TARGET)]
    config = SelectionConfig(target_time=TARGET)
    _assert_results_equal(
        select_winners(candidates, config),
        select_winners_streaming(candidates, config, grid_shape=(3, 3)),
    )


def test_streaming_matches_stack_based_no_coverage_pixel():
    partial_data = _grid_da([[1, 1, 1], [np.nan] * 3, [np.nan] * 3])
    partial_res = _grid_da([[2.0, 2.0, 2.0], [np.nan] * 3, [np.nan] * 3])
    candidates = [Candidate("goes-19", partial_data, partial_res, TARGET)]
    config = SelectionConfig(target_time=TARGET)
    _assert_results_equal(
        select_winners(candidates, config),
        select_winners_streaming(candidates, config, grid_shape=(3, 3)),
    )


def test_streaming_matches_stack_based_three_candidates_distinct_resolutions():
    """Three candidates, all distinct (non-tied) resolutions, full
    overlap -- exercises the winner/runner-up/third-place-discarded path."""
    a = Candidate("goes-19", _grid_da(np.full((3, 3), 10.0)), _grid_da(np.full((3, 3), 4.0)), TARGET)
    b = Candidate("himawari-09", _grid_da(np.full((3, 3), 20.0)), _grid_da(np.full((3, 3), 1.0)), TARGET)
    c = Candidate("meteosat-10", _grid_da(np.full((3, 3), 30.0)), _grid_da(np.full((3, 3), 2.5)), TARGET)
    config = SelectionConfig(target_time=TARGET)
    _assert_results_equal(
        select_winners([a, b, c], config),
        select_winners_streaming([a, b, c], config, grid_shape=(3, 3)),
    )


def test_no_candidates_raises():
    with pytest.raises(ValueError):
        select_winners_streaming([], SelectionConfig(target_time=TARGET), grid_shape=(3, 3))


def test_cropped_candidates_combine_correctly_on_full_grid():
    """The actual scenario select_winners_streaming exists for --
    select_winners can't even accept this (it requires every candidate to
    share one uniform shape). Two candidates covering DIFFERENT,
    partially-overlapping column ranges of a wider grid."""
    grid_shape = (2, 6)
    left = Candidate(
        "goes-19",
        xr.DataArray(np.full((2, 4), 10.0), dims=("y", "x")),  # columns 0-3
        xr.DataArray(np.full((2, 4), 4.0), dims=("y", "x")),
        TARGET,
        columns=np.array([0, 1, 2, 3]),
    )
    right = Candidate(
        "himawari-09",
        xr.DataArray(np.full((2, 4), 20.0), dims=("y", "x")),  # columns 2-5, finer res
        xr.DataArray(np.full((2, 4), 1.0), dims=("y", "x")),
        TARGET,
        columns=np.array([2, 3, 4, 5]),
    )
    result = select_winners_streaming([left, right], SelectionConfig(target_time=TARGET), grid_shape=grid_shape)

    # columns 0-1: only `left` covers them
    assert (result.value.values[:, 0:2] == 10.0).all()
    assert (result.satellite_id.values[:, 0:2] == 1).all()
    # columns 2-3: both cover them, `right` (finer) wins
    assert (result.value.values[:, 2:4] == 20.0).all()
    assert (result.satellite_id.values[:, 2:4] == 6).all()
    assert (result.runner_up_satellite_id.values[:, 2:4] == 1).all()
    # columns 4-5: only `right` covers them
    assert (result.value.values[:, 4:6] == 20.0).all()
    assert (result.satellite_id.values[:, 4:6] == 6).all()
    assert (result.runner_up_satellite_id.values[:, 4:6] == 0).all()


def test_as_slice_if_contiguous_converts_ascending_runs():
    result = _as_slice_if_contiguous(np.array([5, 6, 7, 8]))
    assert result == slice(5, 9)


def test_as_slice_if_contiguous_leaves_wrapped_columns_as_array():
    wrapped = np.array([358, 359, 0, 1, 2])
    result = _as_slice_if_contiguous(wrapped)
    assert isinstance(result, np.ndarray)
    assert np.array_equal(result, wrapped)


def test_as_slice_if_contiguous_handles_empty():
    assert _as_slice_if_contiguous(np.array([], dtype=int)) == slice(0, 0)


def test_accumulator_incremental_usage_matches_select_winners(two_candidates):
    """SelectionAccumulator.update()/finalize() -- the API run_phase2_tier.py
    actually uses so it can fold one candidate in and discard it before
    building the next, rather than collecting a list first (see the
    class's own docstring for why that distinction is the whole point) --
    must produce identical output to select_winners given the exact same
    candidates, whether fed in one at a time or as a pre-built list."""
    config = SelectionConfig(target_time=TARGET, blend_margin_km=3.5)
    stack_result = select_winners(two_candidates, config)

    mid_times = {c.satellite_key: c.mid_time for c in two_candidates}
    surviving_keys, excluded = compute_sync_exclusion(mid_times, TARGET, config.sync_threshold_seconds)
    acc = SelectionAccumulator(grid_shape=(3, 3))
    for c in two_candidates:
        if c.satellite_key in surviving_keys:
            acc.update(c)
    incremental_result = acc.finalize(excluded, config.blend_margin_km)

    _assert_results_equal(stack_result, incremental_result)


def test_compute_sync_exclusion_matches_select_winners_behavior(two_candidates):
    mid_times = {c.satellite_key: c.mid_time for c in two_candidates}
    surviving, excluded = compute_sync_exclusion(mid_times, TARGET, sync_threshold_seconds=60)
    assert excluded == ["himawari-09"]
    assert surviving == {"goes-19"}


def test_cropped_wrapped_columns_write_to_correct_positions():
    """Antimeridian-wrapped columns (see reproject.py's
    visible_columns_for_satellite) must scatter/gather correctly even
    though they aren't a contiguous ascending range."""
    grid_shape = (1, 5)
    wrapped_cols = np.array([3, 4, 0])  # wraps from near the end back to the start
    candidate = Candidate(
        "goes-19",
        xr.DataArray(np.array([[7.0, 8.0, 9.0]]), dims=("y", "x")),
        xr.DataArray(np.full((1, 3), 2.0), dims=("y", "x")),
        TARGET,
        columns=wrapped_cols,
    )
    result = select_winners_streaming([candidate], SelectionConfig(target_time=TARGET), grid_shape=grid_shape)
    assert result.value.values[0, 3] == 7.0
    assert result.value.values[0, 4] == 8.0
    assert result.value.values[0, 0] == 9.0
    assert np.isnan(result.value.values[0, 1])  # untouched column
    assert np.isnan(result.value.values[0, 2])  # untouched column
