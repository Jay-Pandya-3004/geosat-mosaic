"""Custom satpy file handler for INSAT-3D/3DR/3DS Imager L1B HDF5 files.

Registered as the ``insat3d_img_l1b_h5`` reader in
satellites/reader_registry.py's READER_REGISTRY, used in place of
satpy's own stock reader of the same underlying format -- that stock
reader crashes on real files because it doesn't handle the fractional-
seconds suffix on the ``Acquisition_Start_Time``/``Acquisition_End_Time``
attributes (see this module's own fixes below). Handles per-channel LUT
(radiance/albedo/temperature) decoding, per-resolution dataset assembly
(VIS/SWIR at 1km, MIR/TIR1/TIR2 at 4km, WV at 8km), and geostationary
area-definition construction from the file's own orbital metadata.
"""

import datetime as dt
from contextlib import suppress
from functools import cached_property

import dask.array as da
import numpy as np
import xarray as xr
from xarray.core.datatree import DataTree

from satpy.readers.core.file_handlers import BaseFileHandler

LUT_SUFFIXES = {"vis": ("RADIANCE", "ALBEDO"),
                "swir": ("RADIANCE",),
                "mir": ("RADIANCE", "TEMP"),
                "tir1": ("RADIANCE", "TEMP"),
                "tir2": ("RADIANCE", "TEMP"),
                "wv": ("RADIANCE", "TEMP"),
                }

CHANNELS_BY_RESOLUTION = {1000: ["vis", "swir"],
                          4000: ["mir", "tir1", "tir2"],
                          8000: ["wv"],
                          }


def apply_lut(data, lut):
    """Apply a lookup table to raw counts.

    Args:
        data: Array of raw integer counts, used as indices into `lut`.
        lut: Lookup table array mapping counts to physical values.

    Returns:
        `lut` indexed by `data`, same shape as `data`.
    """
    return lut[data]

""" # Original Code Commented Out Due to slow Dask use for Lut conversion
def decode_lut_arr(arr, lut):
    #Decode an array using a lookup table.
    dtype = lut.dtype
    lut_attrs = lut.attrs

    attrs = arr.attrs
    attrs["units"] = lut_attrs["units"]
    attrs["long_name"] = lut_attrs["long_name"]
    new_darr = da.map_blocks(apply_lut, arr.data, lut=np.asanyarray(lut), dtype=dtype)
    new_arr = xr.DataArray(new_darr, dims=arr.dims, attrs=attrs, coords=arr.coords)
    new_arr = new_arr.where(arr.data != attrs["_FillValue"])
    return new_arr
"""
# New code for LUT conversion using numpy for better performance.
def decode_lut_arr(arr, lut):
    """Decode a raw-counts data array through its LUT into physical units.

    NumPy-based replacement for the (slower) dask ``map_blocks`` version
    kept above in a docstring-commented block for reference. Computes
    `arr` to a plain NumPy array first (if it's dask-backed) before
    indexing, applies the LUT's own ``units``/``long_name`` attrs to the
    result, and masks out the LUT's declared fill value.

    Args:
        arr: Raw-counts ``xarray.DataArray`` to decode.
        lut: Lookup table ``xarray.DataArray`` (e.g. an
            ``IMG_<CHANNEL>_RADIANCE``/``_ALBEDO``/``_TEMP`` variable),
            with ``units``/``long_name`` attrs and optionally a
            ``_FillValue`` attr on `arr`.

    Returns:
        A new ``xarray.DataArray`` of decoded physical values, same
        dims/coords as `arr`, with fill-value pixels masked to NaN.
    """
    lut_np = np.asarray(lut)

    data = arr.data
    if hasattr(data, "compute"):  # dask → numpy
        data = data.compute()

    new_data = lut_np[data]

    attrs = arr.attrs.copy()
    attrs["units"] = lut.attrs["units"]
    attrs["long_name"] = lut.attrs["long_name"]

    new_arr = xr.DataArray(
        new_data,
        dims=arr.dims,
        coords=arr.coords,
        attrs=attrs
    )

    # Apply fill value mask
    fill = attrs.get("_FillValue", None)
    if fill is not None:
        new_arr = new_arr.where(arr.data != fill)

    return new_arr

def get_lonlat_suffix(resolution):
    """Get the Longitude/Latitude variable name suffix for a resolution.

    Args:
        resolution: Grid resolution in meters (1000, 4000, or 8000).

    Returns:
        ``"_VIS"`` for 1000m, ``"_WV"`` for 8000m, or ``""`` for any
        other resolution (e.g. 4000m, which has no suffix in the file).
    """
    if resolution == 1000:
        lonlat_suffix = "_VIS"
    elif resolution == 8000:
        lonlat_suffix = "_WV"
    else:
        lonlat_suffix = ""
    return lonlat_suffix


def open_dataset(filename, resolution=1000):
    """Open one resolution group's channels from an INSAT L1B HDF5 file as an xarray Dataset.

    Opens the file twice via h5netcdf -- once with mask/scale decoding
    (to read the LUTs) and once raw (to get the underlying integer
    counts) -- decodes each channel at this resolution through its LUT
    via :func:`decode_lut_arr`, resolves the Longitude/Latitude
    coordinate variables (with a fallback chain to handle INSAT-3DS's
    different WV lon/lat variable naming), and renames dimensions to
    satpy's ``x``/``y``/``Longitude``/``Latitude`` conventions.

    Args:
        filename: Path to the INSAT L1B HDF5 file.
        resolution: Grid resolution in meters; must be one of 1000
            (VIS/SWIR), 4000 (MIR/TIR1/TIR2), or 8000 (WV).

    Returns:
        An ``xarray.Dataset`` containing this resolution's raw and
        LUT-decoded channel variables plus Longitude/Latitude.

    Raises:
        ValueError: If `resolution` is not one of 1000, 4000, 8000.
        KeyError: If no matching Longitude/Latitude variable can be
            found in the file for this resolution.
    """
    if resolution not in [1000, 4000, 8000]:
        raise ValueError(f"Resolution {resolution} not available. Available resolutions: 1000, 4000, 8000")

    h5ds = xr.open_dataset(filename, engine="h5netcdf", chunks="auto")
    h5ds_raw = xr.open_dataset(filename, engine="h5netcdf", chunks="auto", mask_and_scale=False)
    ds = xr.Dataset()
    ds.attrs = h5ds.attrs
    for channel in CHANNELS_BY_RESOLUTION[resolution]:
        var_name = "IMG_" + channel.upper()
        channel_data = h5ds_raw[var_name]
        ds[var_name] = channel_data

        for name in [var_name + "_" + suffix for suffix in LUT_SUFFIXES[channel]]:
            lut = h5ds[name]
            decoded = decode_lut_arr(channel_data, lut)
            ds[name] = decoded

        lonlat_suffix = get_lonlat_suffix(resolution)
        """
        # Original Code Commented Out Due to INSAT-3DS compatibility issue for WV latitude and longitude variable names.
        for coord in ["Longitude", "Latitude"]:
            var_name = coord + lonlat_suffix
            ds[var_name] = h5ds[var_name]
        """

        # New code to handle INSAT-3DS compatibility issue for WV latitude and longitude variable names.
        for coord in ["Longitude", "Latitude"]:
            var_name = coord + lonlat_suffix

            # Fallback logic
            if var_name in h5ds:
                ds[var_name] = h5ds[var_name]
            elif coord in h5ds:
                ds[var_name] = h5ds[coord]
            elif coord + "_VIS" in h5ds:
                ds[var_name] = h5ds[coord + "_VIS"]
            else:
                raise KeyError(f"No coordinate found for {coord} (tried {var_name}, {coord}, {coord+'_VIS'})")
            
    ds = _rename_dims(ds)
    return ds


def _rename_dims(ds):
    """Rename an INSAT dataset's dims/coords to satpy's x/y/Longitude/Latitude conventions.

    Args:
        ds: Dataset with INSAT-native dimension/coordinate names (e.g.
            ``GeoX``/``GeoX1``/``GeoX2``, ``Longitude_VIS``/``Longitude_WV``).

    Returns:
        `ds` with dimensions renamed to ``x``/``y`` and coordinates
        renamed to ``Longitude``/``Latitude`` wherever a matching
        source name is present (renames that don't apply are silently
        skipped via ``suppress(ValueError)``).
    """
    for x_dim in ["GeoX", "GeoX1", "GeoX2"]:
        with suppress(ValueError):
            ds = ds.rename({x_dim: "x"})
    for y_dim in ["GeoY", "GeoY1", "GeoY2"]:
        with suppress(ValueError):
            ds = ds.rename({y_dim: "y"})
    for lons in ["Longitude_VIS", "Longitude_WV"]:
        with suppress(ValueError):
            ds = ds.rename({lons: "Longitude"})
    for lats in ["Latitude_VIS", "Latitude_WV"]:
        with suppress(ValueError):
            ds = ds.rename({lats: "Latitude"})
    return ds


def open_datatree(filename):
    """Open all three resolution groups of an INSAT L1B HDF5 file as a DataTree.

    Args:
        filename: Path to the INSAT L1B HDF5 file.

    Returns:
        An ``xarray.core.datatree.DataTree`` with child nodes ``"1000"``,
        ``"4000"``, ``"8000"`` (one per resolution group from
        :func:`open_dataset`), with the tree's own attrs copied from the
        ``"1000"`` group's attrs.
    """
    datasets = {}
    for resolution in [1000, 4000, 8000]:
        datasets[str(resolution)] = open_dataset(filename, resolution)
    dt = DataTree.from_dict(datasets)
    dt.attrs = dt["1000"].attrs
    return dt


class Insat3DIMGL1BH5FileHandler(BaseFileHandler):
    """satpy file handler for one INSAT-3D/3DR/3DS Imager L1B HDF5 file.

    Lazily opens the file's per-resolution DataTree (see
    :attr:`datatree`), and exposes scan start/end time, per-channel data
    arrays (with LUT decoding and orbital-parameter metadata already
    applied), and a geostationary area definition for satpy's
    ``Scene.load()``.
    """

    @property
    def start_time(self):
        """Scan acquisition start time, parsed from the file's Acquisition_Start_Time attribute.

        Fix for INSAT-3DS compatibility: this attribute's format
        gained a fractional-seconds suffix satpy's own stock reader's
        strptime format string doesn't handle, so the suffix is split
        off before parsing (see original commented-out code just above
        for the pre-fix version)."""
        """ # Original Code Commented Out Due to INSAT-3DS compatibility issue for Acquisition_Start_Time attribute format.
        start_time = dt.datetime.strptime(
            self.datatree.attrs["Acquisition_Start_Time"], "%d-%b-%YT%H:%M:%S")
        """
        # New code to handle INSAT-3DS compatibility issue for Acquisition_Start_Time attribute format
        start_time = dt.datetime.strptime(
            self.datatree.attrs["Acquisition_Start_Time"].split(".")[0], "%d-%b-%YT%H:%M:%S")
        return start_time

    @property
    def end_time(self):
        """Scan acquisition end time, parsed from the file's Acquisition_End_Time attribute.

        Same INSAT-3DS fractional-seconds-suffix fix as `start_time`."""
        """ # Original Code Commented Out Due to INSAT-3DS compatibility issue for Acquisition_End_Time attribute format.
        end_time = dt.datetime.strptime(
            self.datatree.attrs["Acquisition_End_Time"], "%d-%b-%YT%H:%M:%S")
        """
        # New code to handle INSAT-3DS compatibility issue for Acquisition_End_Time attribute format
        end_time = dt.datetime.strptime(
            self.datatree.attrs["Acquisition_End_Time"].split(".")[0], "%d-%b-%YT%H:%M:%S")
        return end_time

    @cached_property
    def datatree(self):
        """Lazily open and cache this file's DataTree (see :func:`open_datatree`)."""
        return open_datatree(self.filename)

    def get_dataset(self, ds_id, ds_info):
        """Load and return one channel's data array with satpy metadata attached.

        Selects the resolution group matching `ds_id["resolution"]`,
        resolves the calibration-specific variable name (raw counts,
        radiance, reflectance/albedo, or brightness temperature), and
        attaches orbital-parameter, platform, and sensor metadata. The
        actual satellite altitude is read from the file's own
        ``Observed_Altitude(km)`` attribute with a sanity-checked
        fallback to the standard GEO altitude (35786 km) when that
        attribute is missing or implausible (real INSAT-3DS files have
        been seen to carry garbage values here, e.g. ~1.5e151).

        Args:
            ds_id: satpy dataset id dict-like object; must have
                ``"resolution"`` and ``"name"`` keys, plus
                ``"calibration"`` unless `ds_id["name"]` is
                ``"longitude"``/``"latitude"``.
            ds_info: satpy dataset info block (unused).

        Returns:
            The requested channel (or coordinate) as a squeezed
            ``xarray.DataArray``, with ``orbital_parameters``,
            ``platform_name``, ``sensor``, and ``resolution_unit``
            attrs set for non-coordinate datasets.
        """
        resolution = ds_id["resolution"]
        ds = self.datatree[str(resolution)]
        if ds_id["name"] in ["longitude", "latitude"]:
            darr = ds[ds_id["name"].capitalize()]

            return darr

        if ds_id["calibration"] == "counts":
            calibration = ""
        elif ds_id["calibration"] == "radiance":
            calibration = "_RADIANCE"
        elif ds_id["calibration"] == "reflectance":
            calibration = "_ALBEDO"
        elif ds_id["calibration"] == "brightness_temperature":
            calibration = "_TEMP"

        darr = ds["IMG_" + ds_id["name"] + calibration]

        nlat, nlon = ds.attrs["Nominal_Central_Point_Coordinates(degrees)_Latitude_Longitude"]
        """ # Original Code Commented Out Due to INSAT-3DS compatibility issue for Observed_Altitude(km) attribute format.
        darr.attrs["orbital_parameters"] = dict(satellite_nominal_longitude=float(nlon),
                                                satellite_nominal_latitude=float(nlat),
                                                satellite_nominal_altitude=float(ds.attrs["Nominal_Altitude(km)"]),
                                                satellite_actual_altitude=float(ds.attrs["Observed_Altitude(km)"]))
        """
        # New code to handle INSAT-3DS compatibility issue for Observed_Altitude(km) attribute format
        # --- FIX actual altitude ---
        obs_alt = ds.attrs.get("Observed_Altitude(km)", 35786)

        try:
            if isinstance(obs_alt, (list, np.ndarray)):
                obs_alt = float(obs_alt[0])
            else:
                obs_alt = float(obs_alt)

            # sanity check
            if not (35000 < obs_alt < 37000):
                raise ValueError

        except:
            obs_alt = 35786

        satellite_actual_altitude = obs_alt * 1000  # convert to meters

        darr.attrs["orbital_parameters"] = dict(satellite_nominal_longitude=float(nlon),
                                                satellite_nominal_latitude=float(nlat),
                                                satellite_nominal_altitude=float(ds.attrs["Nominal_Altitude(km)"]) * 1000,  # convert to meters
                                                satellite_actual_altitude=satellite_actual_altitude)

        darr.attrs["platform_name"] = "insat-3d"
        darr.attrs["sensor"] = "imager"
        darr.attrs["resolution_unit"] = "m" # Updated from "km" to "m" for better clarity and consistency with other datasets, and due to actual resolution is in meters for INSAT-3DS.
        darr = darr.squeeze()

        return darr

    def get_area_def(self, ds_id):
        """Build the geostationary AreaDefinition for a dataset's grid.

        Constructs a geos-projection area definition from the file's
        own scan geometry and orbital metadata. Uses a hardcoded 18-
        degree field-of-view (empirically found to produce better
        geolocation results than the file's own
        ``Field_of_View(degrees)`` attribute, which is kept available in
        a comment for reference) to derive the column/line scaling
        factors, and the file's own ``Observed_Altitude(km)`` attribute
        (sanity-checked, with a fallback to the standard 35786 km GEO
        altitude) for satellite height.

        Args:
            ds_id: satpy dataset id, passed through to
                :meth:`get_dataset` to determine the grid shape.

        Returns:
            A ``pyresample`` geos-projection ``AreaDefinition`` for this
            dataset's grid.

        Note:
            BUG FIX (2026-07-30): the altitude-sanity-check block
            previously referenced an unassigned `h_val`, so the `try`
            always raised NameError and silently fell through to the
            nominal-altitude fallback for every file, not just ones with
            a bad attribute. It happened to still "work" because real
            INSAT-3DS files (see a real 01-JAN-2026 sample) do carry a garbage Observed_Altitude(km) value
            (e.g. 1.5e151), which the sanity check is specifically meant
            to catch and fall back from anyway -- but relying on an
            unrelated NameError to reach that fallback was an accident,
            not the sanity check actually running. Now `h_val` is
            assigned first, so a genuinely valid value gets a real
            chance to pass the check.
        """
        from satpy.readers.core._geos_area import get_area_definition, get_area_extent
        darr = self.get_dataset(ds_id, None)
        shape = darr.shape
        lines = shape[-2]
        cols = shape[-1]

        # From empirical analysis, hardcoding the view of view to 18 degrees
        # produces better geolocation results.
        # Uncommenting the line below will use the fov from the file instead,
        # this line is kept for reference.
        #fov = self.datatree.attrs["Field_of_View(degrees)"]
        fov = 18
        cfac = 2 ** 16 / (fov / cols)

        # From reverse engineering metadata from a netcdf file, we discovered
        # the lfac is actually the same as cfac, ie dependent on cols, not lines!
        lfac = 2 ** 16 / (fov / cols)

        """ # Original Code Commented Out Due to INSAT-3DS compatibility issue for Observed_Altitude(km) attribute format.
        h = self.datatree.attrs["Observed_Altitude(km)"] * 1000
        """
        # New code to handle INSAT-3DS compatibility issue for Observed_Altitude(km) attribute format.
        #
        # BUG FIX (2026-07-30): this block previously referenced `h_val`
        # without ever assigning it first, so the `try` always raised
        # NameError and silently fell through to the nominal-altitude
        # fallback -- for every file, not just ones with a bad attribute.
        # It happened to still "work" because real INSAT-3DS files (see a
        # real 01-JAN-2026 sample) do carry a garbage
        # Observed_Altitude(km) value (e.g. 1.5e151), which the sanity
        # check below is specifically meant to catch and fall back from
        # anyway -- but relying on an unrelated NameError to reach that
        # fallback was an accident, not the sanity check actually running.
        # Now h_val is assigned first, so the real value gets a genuine
        # chance to pass the sanity check when a file's attribute is valid.
        h_val = self.datatree.attrs.get("Observed_Altitude(km)")
        try:
            # Handle array or weird types
            if isinstance(h_val, (list, np.ndarray)):
                h_val = float(h_val[0])
            else:
                h_val = float(h_val)

            h = h_val * 1000

            # sanity check
            if not (3.5e7 < h < 3.7e7):
                raise ValueError("Invalid altitude")

        except Exception:
            # fallback to standard GEO altitude
            h = 35786000.0

        # WGS 84
        a = 6378137.0
        b = 6356752.314245

        subsatellite_longitude = self.datatree.attrs["Nominal_Central_Point_Coordinates(degrees)_Latitude_Longitude"][1]

        pdict = {
            "cfac": cfac,
            "lfac": lfac,
            "coff": cols // 2 + 1,
            "loff": lines // 2,
            "ncols": cols,
            "nlines": lines,
            "scandir": "N2S",
            "a": a,
            "b": b,
            "h": h,
            "ssp_lon": subsatellite_longitude,
            "a_name": "insat3d82",
            "a_desc": "insat3d82",
            "p_id": "geosmsg"
        }
        area_extent = get_area_extent(pdict)
        adef = get_area_definition(pdict, area_extent)
        return adef
