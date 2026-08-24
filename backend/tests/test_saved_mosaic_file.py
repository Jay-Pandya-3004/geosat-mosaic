"""Checks a specific already-written mosaic file's satellite_id/blend
fields for internal consistency -- distinct from
test_pipeline_integration.py, which regenerates fresh in-memory data each
run. This targets one saved artifact directly (as a user asked to check
after reviewing its rendered PNG), so a change to the pipeline that
corrupts a *previously written* file would be caught even if a fresh
regeneration happens to look fine.

Skips if the named file isn't present (e.g. data/mosaics/ was cleaned, or
this is a fresh checkout) rather than failing -- this is a check on one
specific artifact, not a hermetic test fixture the suite is expected to
recreate.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

MOSAIC_PATH = Path(__file__).resolve().parents[1] / "data" / "mosaics" / "mosaic_20260730T200600.nc"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mosaic():
    if not MOSAIC_PATH.exists():
        pytest.skip(f"{MOSAIC_PATH} not present -- nothing to check")
    return xr.open_dataset(MOSAIC_PATH)


def test_satellite_id_values_are_all_declared(mosaic):
    """Every non-zero satellite_id/runner_up_satellite_id must be one of
    the ids actually listed in the file's own `satellite` coordinate --
    otherwise the mask points at metadata that doesn't exist in the file."""
    declared_ids = set(mosaic["satellite"].values.tolist())
    present_ids = set(np.unique(mosaic["satellite_id"].values).tolist()) - {0}
    present_runner_ids = set(np.unique(mosaic["runner_up_satellite_id"].values).tolist()) - {0}
    assert present_ids <= declared_ids, f"undeclared satellite_id(s): {present_ids - declared_ids}"
    assert present_runner_ids <= declared_ids, f"undeclared runner_up id(s): {present_runner_ids - declared_ids}"


def test_no_value_leakage_where_covered(mosaic):
    covered = mosaic["satellite_id"].values != 0
    assert np.isfinite(mosaic["value"].values[covered]).all()
    assert np.isnan(mosaic["value"].values[~covered]).all()


def test_is_blended_only_where_covered(mosaic):
    """Can't blend a pixel with no coverage at all."""
    no_coverage = mosaic["satellite_id"].values == 0
    assert (mosaic["is_blended"].values[no_coverage] == 0).all()


def test_runner_up_zero_exactly_where_not_blended(mosaic):
    is_blended = mosaic["is_blended"].values.astype(bool)
    runner_up = mosaic["runner_up_satellite_id"].values
    assert (runner_up[~is_blended] == 0).all(), "non-blended pixels must have runner_up_satellite_id == 0"
    assert (runner_up[is_blended] != 0).all(), "blended pixels must name a real runner-up satellite"


def test_runner_up_never_equals_winner(mosaic):
    sat_id = mosaic["satellite_id"].values
    runner_up = mosaic["runner_up_satellite_id"].values
    is_blended = mosaic["is_blended"].values.astype(bool)
    assert (sat_id[is_blended] != runner_up[is_blended]).all(), "a satellite can't be its own runner-up"


def test_blend_weight_winner_bounds(mosaic):
    """weight_winner = (1/winner_res) / (1/winner_res + 1/runner_res).
    Since the winner is defined as the finer-or-equal actual resolution,
    weight_winner must always be >= 0.5, and exactly 1.0 wherever
    is_blended is 0."""
    w = mosaic["blend_weight_winner"].values
    is_blended = mosaic["is_blended"].values.astype(bool)
    covered = mosaic["satellite_id"].values != 0

    assert np.allclose(w[covered & ~is_blended], 1.0, atol=1e-3)
    blended_w = w[is_blended]
    assert blended_w.size > 0, "expected at least some blended pixels in this file"
    assert (blended_w >= 0.5 - 1e-6).all()
    assert (blended_w <= 1.0 + 1e-6).all()


def test_reasonable_fraction_of_pixels_blended(mosaic):
    """Sanity bound, not a tight spec: seams should be a small minority of
    the covered disk, not everything and not nothing."""
    covered = mosaic["satellite_id"].values != 0
    is_blended = mosaic["is_blended"].values.astype(bool)
    fraction = is_blended[covered].sum() / covered.sum()
    assert 0.0 < fraction < 0.25, f"blended fraction of covered pixels was {fraction:.3f}"


def test_full_global_coverage_with_five_satellites(mosaic):
    """Regression check on this specific run: 5 real satellites (GOES-18,
    GOES-19, Meteosat-10, Meteosat-9, Himawari-9), zero no-coverage pixels
    across the +-66.5 deg grid."""
    assert set(mosaic["satellite_key"].values.tolist()) == {
        "goes-18",
        "goes-19",
        "meteosat-10",
        "meteosat-09",
        "himawari-09",
    }
    assert int((mosaic["satellite_id"].values == 0).sum()) == 0


def test_plausible_brightness_temperatures(mosaic):
    finite = mosaic["value"].values[np.isfinite(mosaic["value"].values)]
    assert finite.min() > 100.0
    assert finite.max() < 400.0


def test_satellite_scan_mid_is_between_start_and_end(mosaic):
    """satellite_scan_mid should come from midtime.py's own computed
    ScanTimes.mid (not just any value) -- the one thing every consumer
    can check without re-deriving it is that it falls strictly between
    that satellite's own scan_start and scan_end."""
    assert "satellite_scan_mid" in mosaic
    start = mosaic["satellite_scan_start"].values
    end = mosaic["satellite_scan_end"].values
    mid = mosaic["satellite_scan_mid"].values
    assert (mid >= start).all()
    assert (mid <= end).all()


def test_dataset_time_coordinate_is_median_of_satellite_scan_mid(mosaic):
    """The file's authoritative `time` coordinate must be the MEDIAN of
    satellite_scan_mid (per the design decision to prefer median over
    mean or the requested target_time -- see mosaic_io.py's `time`
    coordinate attrs), not just any timestamp near the target."""
    mids = np.sort(mosaic["satellite_scan_mid"].values)
    n = mids.size
    expected_median = mids[n // 2] if n % 2 == 1 else mids[n // 2 - 1] + (mids[n // 2] - mids[n // 2 - 1]) / 2
    assert mosaic["time"].values == expected_median
    assert "requested_target_time" in mosaic.attrs
