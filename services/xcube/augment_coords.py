"""xcube dataset-augmentation hook module.

Provides `set_lat_lon_index`, registered with xcube as an augmentation
hook that runs after each mosaic dataset is opened, so that the 'lat'/
'lon' coordinate variables xcube's tile-serving and statistics code paths
depend on are real, indexed dimensions rather than just auxiliary
coordinates on dims still named 'y'/'x'.
"""


def set_lat_lon_index(ds):
    """xcube Augmentation hook: mosaic_io.py writes 'lat'/'lon' as real
    1-D coordinate variables on dims 'y'/'x', but names them differently
    from their dimension -- xarray only treats a coordinate as an
    indexable dimension-coordinate when its name matches its dimension's
    name, so 'lat'/'lon' come in as non-indexed auxiliary coordinates.

    Originally this just did `ds.set_index(y="lat", x="lon")`, which was
    enough to fix tile serving (xcube's tile code does `.sel()` lookups,
    which only need a real index, not a same-named dimension). That alone
    was NOT enough for xcube's /statistics POST endpoint (geometry-based
    min/max/mean/etc, the natural replacement for THREDDS's slow
    GetMetadata&item=minmax) -- its geometry-clipping code
    (xcube/core/geom.py's _clip_dataset_by_geometry) does
    `dataset.isel(lat=..., lon=...)`, which requires 'lat'/'lon' to be
    real DIMENSION names, not just index labels on dims still called
    'y'/'x'. `rename` first, THEN `set_index` (rename alone does not
    create an index -- xarray warns about this explicitly) satisfies
    both: dims are literally named lat/lon (for isel-based code) AND
    they're real indexes (for sel-based code). Verified both paths still
    work after this change, not just the new one.

    Args:
        ds: The just-opened xarray Dataset for one mosaic file, with 'y'/
            'x' dimensions and separately-named 'lat'/'lon' 1-D coordinate
            variables (as written by mosaic_io.py).

    Returns:
        A new xarray Dataset with 'y'/'x' renamed to 'lat'/'lon' and
        indexed on those same names.
    """
    return ds.rename({"y": "lat", "x": "lon"}).set_index(lat="lat", lon="lon")
