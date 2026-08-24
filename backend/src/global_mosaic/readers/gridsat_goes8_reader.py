"""Custom satpy file handler for the GridSat-GOES/8 reprocessed legacy archive.

Loads the NCEI GridSat-GOES reprocessed NetCDF files used for the legacy
GOES imager satellites (goes-08..15, see acquisition/goes.py's
download_legacy/find_nearest_legacy_entry) -- a format with no stock
satpy reader. Registered as the ``gridsat_goes8`` reader in
satellites/reader_registry.py's READER_REGISTRY for goes-08 (and,
unverified, goes-09..15, which are assumed to share the same file
format).
"""

import xarray as xr
import numpy as np
import datetime as dt
from pyresample.geometry import AreaDefinition
from satpy.readers.core.file_handlers import BaseFileHandler

CHANNEL_MAP = {
    # ---- Standard GridSat variables ----
    "ch1": ["vis", "ch1"],
    "ch2": ["irwin", "ch2"],
    "ch3": ["wv", "ch3"],
    "ch4": ["irwin_cdr", "irwin", "ch4"],
    "ch5": ["irwin2", "ch5"],
}

class GridSatGOESFileHandler(BaseFileHandler):
    """satpy file handler for one GridSat-GOES reprocessed NetCDF file.

    Opens the file via xarray and exposes its channel variables,
    scan-time attributes, orbital parameters, and a lat/lon
    ``AreaDefinition`` in the shape satpy's ``Scene.load()`` expects.
    """

    def __init__(self, filename, filename_info, filetype_info):
        """Open the GridSat NetCDF file for this file handler.

        Args:
            filename: Path to the GridSat-GOES NetCDF file.
            filename_info: Parsed filename fields, per satpy's YAML
                config file-pattern matching.
            filetype_info: File-type config block from the reader's
                YAML config.
        """
        super().__init__(filename, filename_info, filetype_info)
        self.ds = xr.open_dataset(filename, decode_timedelta=False)

    # --------------------------------------------------
    # TIME HANDLING
    # --------------------------------------------------
    @property
    def start_time(self):
        """Scan coverage start time, from the file's attrs or its `time` variable."""
        if "time_coverage_start" in self.ds.attrs:
            return dt.datetime.fromisoformat(
                self.ds.attrs["time_coverage_start"].replace("Z", "")
            )

        if "time" in self.ds:
            t = self.ds["time"].values
            if np.issubdtype(t.dtype, np.datetime64):
                return t[0].astype("datetime64[s]").astype(object)

        return None


    @property
    def end_time(self):
        """Scan coverage end time, from the file's attrs, or `start_time` if absent."""
        if "time_coverage_end" in self.ds.attrs:
            return dt.datetime.fromisoformat(
                self.ds.attrs["time_coverage_end"].replace("Z", "")
            )

        return self.start_time

    # --------------------------------------------------
    # DATA LOADING
    # --------------------------------------------------
    def get_dataset(self, ds_id, ds_info):
        """Load and return one channel's data array, with satpy metadata attached.

        Resolves `ds_id`'s name through ``CHANNEL_MAP`` to the actual
        variable present in this file (GridSat uses inconsistent
        variable names across its channel/CDR variants), then attaches
        platform/sensor/units/orbital-parameter metadata satpy and
        downstream code expect.

        Args:
            ds_id: satpy dataset id dict-like object; only ``ds_id["name"]``
                is used.
            ds_info: satpy dataset info block (unused).

        Returns:
            The channel's data as an ``xarray.DataArray`` (squeezed),
            with ``platform_name``, ``sensor``, ``units``, and
            ``orbital_parameters`` attrs set.

        Raises:
            KeyError: If `ds_id`'s name is not in ``CHANNEL_MAP``, or
                none of its candidate variable names exist in the file.
        """
        name = ds_id["name"]

        if name not in CHANNEL_MAP:
            raise KeyError(f"Unknown dataset {name}")

        var_candidates = CHANNEL_MAP[name]

        var_name = None
        for v in var_candidates:
            if v in self.ds:
                var_name = v
                break

        if var_name is None:
            raise KeyError(f"No matching variable found for {name}: {var_candidates}")

        data = self.ds[var_name]

        # --------------------------------------------------
        # PLATFORM / SENSOR (from global attrs)
        # --------------------------------------------------
        data.attrs["platform_name"] = self.ds.attrs.get("platform", "GOES-08")

        instrument = self.ds.attrs.get("instrument", "")
        if "Imager" in instrument:
            data.attrs["sensor"] = "imager"

        # --------------------------------------------------
        # UNITS (use file metadata, fallback if needed)
        # --------------------------------------------------
        if "units" not in data.attrs:
            if name == "ch1":
                data.attrs["units"] = "1"   # reflectance
            else:
                data.attrs["units"] = "K"

        # --------------------------------------------------
        # ORBITAL PARAMETERS (from variables)
        # --------------------------------------------------
        satlon = float(self.ds["satlon"].values.squeeze().item()) if "satlon" in self.ds else -75.0
        satlat = float(self.ds["satlat"].values.squeeze().item()) if "satlat" in self.ds else 0.0
        satrad = float(self.ds["satrad"].values.squeeze().item()) if "satrad" in self.ds else 42164.0  # km

        EARTH_RADIUS_KM = 6378.137

        # satrad = distance from Earth center (km)
        sat_height_km = satrad - EARTH_RADIUS_KM

        # convert to meters
        sat_alt_m = sat_height_km * 1000

        data.attrs["orbital_parameters"] = {
            "satellite_nominal_longitude": satlon,
            "satellite_nominal_latitude": satlat,
            "satellite_nominal_altitude": sat_alt_m,
        }

        return data.squeeze()

    # --------------------------------------------------
    # AREA DEFINITION (IMPORTANT)
    # --------------------------------------------------
    def get_area_def(self, ds_id):
        """Build the lat/lon AreaDefinition covering this file's grid.

        Args:
            ds_id: satpy dataset id (unused; the file's own ``lat``/
                ``lon`` coordinate variables define the grid for every
                dataset in this file).

        Returns:
            A ``pyresample.geometry.AreaDefinition`` in plain
            lat/lon (WGS84) projection, sized to the file's own lat/lon
            coordinate arrays.
        """

        nlat = self.ds.sizes["lat"]
        nlon = self.ds.sizes["lon"]

        lon_min = float(self.ds["lon"].min())
        lon_max = float(self.ds["lon"].max())
        lat_min = float(self.ds["lat"].min())
        lat_max = float(self.ds["lat"].max())

        proj_dict = {
            "proj": "latlong",
            "datum": "WGS84",
        }

        area_extent = (lon_min, lat_min, lon_max, lat_max)

        return AreaDefinition(
            "gridsat_goes",
            "GridSat GOES-08",
            "latlon",
            proj_dict,
            nlon,
            nlat,
            area_extent,
        )