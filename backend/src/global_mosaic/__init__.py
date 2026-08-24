"""Global Mosaic pipeline package.

Top-level namespace for the satellite-imagery global mosaic pipeline. The
subpackages under this namespace cover the full pipeline: ``acquisition``
(downloading raw satellite files from GOES/Himawari/Meteosat/INSAT),
``satellites`` (satellite metadata, coverage, and SRF radiometric
correction), ``processing`` (reprojection, satellite selection, blending,
and mosaic I/O), and ``readers`` (custom satpy readers for satellite
formats not natively supported by satpy).
"""
