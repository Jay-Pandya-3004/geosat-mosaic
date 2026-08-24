"""Raw satellite file acquisition subpackage.

Contains per-satellite-family downloaders (GOES, Himawari, Meteosat,
INSAT) that fetch raw imagery files from their respective public data
sources (e.g. AWS S3 buckets, MOSDAC) for later processing by the
``global_mosaic.processing`` pipeline.
"""
