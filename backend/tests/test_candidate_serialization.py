"""Unit tests for run_phase2_tier.py's per-satellite subprocess isolation
support: packing a built Candidate + SatelliteMeta into a small .npz for
a subprocess boundary (see run_single_satellite_candidate /
build_band_mosaic's isolate_satellites path) and reading it back
byte-identically, plus the automatic pixel-count threshold that decides
which tiers get this isolation. All synthetic -- no network/satpy data
needed."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import run_phase2_tier as tier_script  # noqa: E402

from global_mosaic.processing.select import Candidate, SelectionAccumulator  # noqa: E402
from global_mosaic.processing.mosaic_io import SatelliteMeta  # noqa: E402

TARGET = dt.datetime(2026, 1, 1, 0, 11, 0)


def _sample_candidate_and_meta():
    value = np.arange(35, dtype=np.float32).reshape(5, 7)
    resolution_km = (np.arange(35, dtype=np.float32).reshape(5, 7) * 0.1) + 1.0
    columns = np.arange(100, 107)
    candidate = Candidate(
        satellite_key="goes-19",
        data=xr.DataArray(value, dims=("y", "x")),
        resolution_km=xr.DataArray(resolution_km, dims=("y", "x")),
        mid_time=dt.datetime(2026, 1, 1, 0, 10, 30),
        source_filename="ABI-L1b-goes19.nc",
        columns=columns,
    )
    meta = SatelliteMeta(
        satellite_key="goes-19",
        satellite_id=1,
        longitude_deg=-75.2,
        scan_start=dt.datetime(2026, 1, 1, 0, 10, 0),
        scan_end=dt.datetime(2026, 1, 1, 0, 11, 0),
        scan_mid=dt.datetime(2026, 1, 1, 0, 10, 30),
        nominal_spatial_resolution_km=0.5,
        band_center_wavelength_um=0.64,
        temporal_resolution_minutes=5.0,
        source_filename="ABI-L1b-goes19.nc",
    )
    return candidate, meta


def test_candidate_npz_round_trip(tmp_path):
    candidate, meta = _sample_candidate_and_meta()
    out_path = tmp_path / "candidate.npz"

    tier_script._write_candidate_npz(candidate, meta, "%", out_path)
    read_candidate, read_meta, read_units = tier_script._read_candidate_npz(out_path)

    assert read_candidate.satellite_key == candidate.satellite_key
    np.testing.assert_array_equal(read_candidate.data.values, candidate.data.values)
    np.testing.assert_array_equal(read_candidate.resolution_km.values, candidate.resolution_km.values)
    np.testing.assert_array_equal(read_candidate.columns, candidate.columns)
    assert read_candidate.mid_time == candidate.mid_time
    assert read_candidate.source_filename == candidate.source_filename
    assert read_meta == meta
    assert read_units == "%"


def test_candidate_npz_round_trip_preserves_selection_result(tmp_path):
    """The real point of the round trip isn't the fields themselves, it's
    that folding a reconstructed Candidate into SelectionAccumulator
    produces the same result as folding the original -- proves the
    serialization loses nothing SelectionAccumulator.update() actually
    reads (.data.values / .resolution_km.values / .columns /
    .satellite_key)."""
    candidate, meta = _sample_candidate_and_meta()
    out_path = tmp_path / "candidate.npz"
    tier_script._write_candidate_npz(candidate, meta, "%", out_path)
    read_candidate, _, _ = tier_script._read_candidate_npz(out_path)

    grid_shape = (5, 200)
    direct = SelectionAccumulator(grid_shape)
    direct.update(candidate)
    direct_result = direct.finalize(excluded_by_sync=[], blend_margin_km=0.0)

    round_tripped = SelectionAccumulator(grid_shape)
    round_tripped.update(read_candidate)
    round_tripped_result = round_tripped.finalize(excluded_by_sync=[], blend_margin_km=0.0)

    np.testing.assert_array_equal(direct_result.value.values, round_tripped_result.value.values)
    np.testing.assert_array_equal(direct_result.satellite_id.values, round_tripped_result.satellite_id.values)
    np.testing.assert_array_equal(direct_result.resolution_km.values, round_tripped_result.resolution_km.values)


@pytest.mark.parametrize(
    "tier,expected_isolate",
    [
        ("A", False),  # 2km grid, ~148M px -- stays under threshold, runs in-process
        ("B", True),  # 1km grid, ~593M px -- crosses threshold, isolated per satellite
    ],
)
def test_per_satellite_isolation_threshold_matches_real_tier_grids(tier, expected_isolate):
    resolution_km, _ = tier_script.TIERS[tier]
    target_area = tier_script.common_grid(resolution_km=resolution_km)
    pixel_count = target_area.height * target_area.width
    assert (pixel_count > tier_script.PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD) == expected_isolate
