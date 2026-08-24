# Custom satpy readers

Migrated from the original prototype's `satpy_modified_reader_bkp/` folder.
satpy doesn't ship stock readers for either of these, so they're maintained
here instead.

## Files

- `gridsat_goes8_reader.py` + `gridsat_goes8.yaml` -- reads the NCEI GridSat
  reprocessed GOES-8..15 archive (NetCDF). The yaml's `file_reader` entry
  currently points at `satpy.readers.gridsat_goes8_reader.GridSatGOESFileHandler`
  -- i.e. it was set up by dropping both files directly into the installed
  satpy package (`site-packages/satpy/readers/` + `site-packages/satpy/etc/readers/`),
  which is how the original prototype worked. That's fragile (a `pip install
  --upgrade satpy` wipes it), so before relying on this long-term, switch to
  satpy's supported custom-config mechanism instead:
  1. Point `SATPY_CONFIG_PATH` at a directory containing `readers/gridsat_goes8.yaml`
     (this `readers/` folder works if you set `SATPY_CONFIG_PATH` to this
     package's root, one level up).
  2. Change the yaml's `file_reader` to `!!python/name:global_mosaic.readers.gridsat_goes8_reader.GridSatGOESFileHandler`
     so it resolves against this package instead of satpy's own namespace.
  3. Make sure `global_mosaic` is installed/importable (`pip install -e .`
     from `backend/`) so that python/name reference resolves.
  Only verified against GOES-08 files so far -- unverified whether it also
  handles GOES-09 through GOES-15 (same nominal file format per NCEI, but
  not tested against real files from those satellites yet).

- `insat3d_img_l1b_h5.py` + `insat3d_img_l1b_h5.yaml` -- reads
  INSAT-3D/3DR/3DS L1B HDF5 imager files. This is global_mosaic's own copy
  of satpy's stock `insat3d_img_l1b_h5` reader, patched because the stock
  reader crashes on real files: it parses
  `Acquisition_Start_Time`/`Acquisition_End_Time` with
  `"%d-%b-%YT%H:%M:%S"`, but real files carry a fractional-seconds suffix
  (e.g. `"01-JAN-2026T00:00:35.473"`) that format string can't parse --
  this reader strips it first. Unlike `gridsat_goes8_reader.py` above,
  this one already uses the supported `SATPY_CONFIG_PATH` mechanism rather
  than the fragile monkey-patch-into-site-packages approach, so no
  migration is needed here -- point `SATPY_CONFIG_PATH` at this package's
  root (one level up) the same way, and it resolves correctly.
