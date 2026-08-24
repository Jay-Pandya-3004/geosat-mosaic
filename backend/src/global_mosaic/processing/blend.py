"""Seam blending at satellite overlap boundaries.

Feathers between select.py's winner and its runner-up wherever select.py
flagged a near-tie (SelectionResult.tie_mask), weighted by each source's
actual per-pixel resolution -- the finer (sharper) source gets more
weight. This is a deliberate stand-in for this module's original planned
design ("weighted by distance-to-edge or inverse view-zenith"): in this
project's resolution.py model, actual resolution is *derived directly*
from view-zenith angle (see that module's docstring), so a source with
better (smaller) actual resolution at a pixel is, by construction, the
one viewing that pixel closer to its own nadir -- inverse-resolution
weighting achieves the same effect as inverse-view-zenith weighting
without needing view zenith angle threaded through as a separate value.

This is also deliberately NOT a fixed-width spatial transition band (the
other option select.py's original docstring sketched) -- a resolution-
based tie is intrinsically wider or narrower depending on how steeply
each satellite's resolution degrades near that specific pixel, which is
more physically meaningful than an arbitrary constant pixel-count margin,
and needs no separate geometric distance-transform computation over the
satellite_id mask.

Outside tie_mask, this is a pure pass-through -- select.py's hard-cut
winner value is used unchanged.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from global_mosaic.processing.select import SelectionResult


def feather_seams(selected: SelectionResult) -> xr.DataArray:
    """Feather select.py's hard winner/runner-up transition at near-tie pixels.

    Returns a new value array: select.py's hard winner, blended with
    its runner-up at tie_mask pixels using inverse-actual-resolution
    weights. Returns `selected.value` unchanged if there's no runner-up
    to blend with (fewer than 2 candidates were selected from) or no
    pixel was ever flagged as a tie (blend_margin_km was 0, or no pixel's
    winner/runner-up were close enough).

    Args:
        selected: A :class:`~global_mosaic.processing.select.SelectionResult`
            from ``select_winners``/``select_winners_streaming``.

    Returns:
        A new ``xarray.DataArray``, same shape/coords/dims as
        ``selected.value``: unchanged outside ``tie_mask``, and an
        inverse-actual-resolution-weighted blend of winner and runner-up
        within it.
    """
    if selected.runner_up_value is None or not bool(selected.tie_mask.values.any()):
        return selected.value

    winner_val = selected.value.values
    winner_res = selected.resolution_km.values
    runner_val = selected.runner_up_value.values
    runner_res = selected.runner_up_resolution_km.values
    tie = selected.tie_mask.values

    # Only pixels in `tie` ever reach this division, and tie_mask is only
    # ever True where both winner_res and runner_res are finite/positive
    # (select.py only sets it under has_runner_up), so no 0/0 or NaN/0 risk.
    weight_winner = 1.0 / winner_res
    weight_runner = 1.0 / runner_res
    blended = (weight_winner * winner_val + weight_runner * runner_val) / (weight_winner + weight_runner)

    out = np.where(tie, blended, winner_val)
    return xr.DataArray(out, coords=selected.value.coords, dims=selected.value.dims)
