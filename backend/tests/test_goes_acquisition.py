"""Unit test for acquisition/goes.py's download-cache-skip behavior.
Synthetic/mocked -- no real network calls."""

from __future__ import annotations

import datetime as dt

import global_mosaic.acquisition.goes as goes_module
from global_mosaic.acquisition.goes import download_abi


class _FakeS3FS:
    def __init__(self):
        self.get_calls: list[tuple[str, str]] = []

    def get(self, key, local_path):
        self.get_calls.append((key, local_path))


def test_download_abi_skips_already_cached_file(tmp_path, monkeypatch):
    """download_abi used to unconditionally re-fetch from S3 even when the
    file already existed locally -- GOES full-disk files run 20-400+ MB
    depending on band, so this was a genuine wasted-time bug for any
    re-run of an already-processed timestamp, unlike meteosat.py/
    insat.py's download(), which already skip existing files."""
    key = "bucket/ABI-L1b-RadF/2026/001/00/OR_ABI-L1b-RadF-M6C13_G18_s20260010010217_e20260010019537_c20260010019594.nc"
    monkeypatch.setattr(goes_module, "find_nearest_abi_file", lambda *a, **k: key)
    fake_fs = _FakeS3FS()
    monkeypatch.setattr(goes_module, "get_s3fs", lambda anonymous=True: fake_fs)

    local_name = key.split("/")[-1]
    (tmp_path / local_name).write_bytes(b"already here")

    result = download_abi("goes-18", dt.datetime(2026, 1, 1, 0, 10), str(tmp_path))

    from pathlib import Path

    assert Path(result) == tmp_path / local_name
    assert fake_fs.get_calls == [], "cached file must not be re-fetched"


def test_download_abi_fetches_when_not_cached(tmp_path, monkeypatch):
    key = "bucket/ABI-L1b-RadF/2026/001/00/OR_ABI-L1b-RadF-M6C13_G18_s20260010010217_e20260010019537_c20260010019594.nc"
    monkeypatch.setattr(goes_module, "find_nearest_abi_file", lambda *a, **k: key)
    fake_fs = _FakeS3FS()
    monkeypatch.setattr(goes_module, "get_s3fs", lambda anonymous=True: fake_fs)

    download_abi("goes-18", dt.datetime(2026, 1, 1, 0, 10), str(tmp_path))

    assert len(fake_fs.get_calls) == 1
