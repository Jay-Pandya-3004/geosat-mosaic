"""Unit tests for processing/blend.py's seam feathering. All synthetic."""

from __future__ import annotations

import datetime as dt

import numpy as np
import xarray as xr

from global_mosaic.processing.blend import feather_seams
from global_mosaic.processing.select import Candidate, SelectionConfig, select_winners

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _grid_da(values):
    coords = {"y": np.arange(3), "x": np.arange(3)}
    return xr.DataArray(np.array(values, dtype=float), coords=coords, dims=["y", "x"])


def test_blend_weighted_by_inverse_resolution():
    """A=4km res val=10, B=1km res val=20 at a tied pixel -- weight_A=1/4,
    weight_B=1/1, blended = (0.25*10 + 1.0*20) / 1.25 = 18.0 (hand-computed
    and verified during development)."""
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    b_data = _grid_da(np.full((3, 3), 20.0))
    b_res = _grid_da(np.full((3, 3), 1.0))
    candidates = [Candidate("goes-19", a_data, a_res, TARGET), Candidate("himawari-09", b_data, b_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET, blend_margin_km=3.5))
    blended = feather_seams(result)
    assert np.allclose(blended.values, 18.0)


def test_passthrough_outside_tie_mask():
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    b_data = _grid_da([[20, 20, 20], [np.nan] * 3, [np.nan] * 3])
    b_res = _grid_da([[1.0, 1.0, 1.0], [np.nan] * 3, [np.nan] * 3])
    candidates = [Candidate("goes-19", a_data, a_res, TARGET), Candidate("himawari-09", b_data, b_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET, blend_margin_km=3.5))
    blended = feather_seams(result)
    assert np.allclose(blended.values[0], 18.0)  # tied row
    assert np.allclose(blended.values[1:], 10.0)  # untouched, pure A


def test_no_blend_when_margin_disabled():
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    b_data = _grid_da(np.full((3, 3), 20.0))
    b_res = _grid_da(np.full((3, 3), 1.0))
    candidates = [Candidate("goes-19", a_data, a_res, TARGET), Candidate("himawari-09", b_data, b_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET))  # blend_margin_km=0.0 default
    blended = feather_seams(result)
    assert np.array_equal(blended.values, result.value.values, equal_nan=True)


def test_no_runner_up_passthrough():
    a_data = _grid_da(np.full((3, 3), 10.0))
    a_res = _grid_da(np.full((3, 3), 4.0))
    result = select_winners([Candidate("goes-19", a_data, a_res, TARGET)], SelectionConfig(target_time=TARGET))
    blended = feather_seams(result)
    assert np.array_equal(blended.values, result.value.values, equal_nan=True)
