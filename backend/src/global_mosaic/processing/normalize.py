"""Loads each satellite's raw files into a common xarray representation
(DataArray + its actual geostationary projection/navigation params) via satpy.

Not yet implemented -- this is the next piece of work after acquisition is
proven out for all 6 slots. Each satellite needs its own satpy reader:

- GOES: 'abi_l1b'
- Himawari: 'ahi_hsd'
- Meteosat: 'seviri_l1b_native' or 'seviri_l1b_hrit' depending on what
  the EUMETSAT Data Store hands back
- INSAT: satpy does not ship a stock INSAT-3D/3DR reader as of writing --
  check for a community reader or plan to write a minimal one against the
  HDF5 L1B format.
"""

from __future__ import annotations

import xarray as xr


def load_ir_window(satellite_key: str, local_paths: list[str]) -> xr.DataArray:
    """Load a satellite's IR-window channel into a common xarray representation.

    Not yet implemented -- see this module's docstring for the intended
    per-satellite reader mapping.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-16"``.
        local_paths: Local file path(s) to load, as returned by the
            corresponding ``acquisition`` module's ``download()``.

    Returns:
        The loaded IR-window channel as an ``xarray.DataArray`` with its
        geostationary projection/navigation parameters attached.

    Raises:
        NotImplementedError: Always, until this function is implemented.
    """
    raise NotImplementedError(f"normalize.load_ir_window not yet implemented for {satellite_key}")
