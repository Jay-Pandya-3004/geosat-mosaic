"""Unit tests for acquisition/himawari.py. Synthetic/mocked -- no real
network calls (the satellite_key guard test doesn't reach get_s3fs at
all; the cache-skip test mocks get_s3fs/find_segment_keys)."""

from __future__ import annotations

import datetime as dt

import global_mosaic.acquisition.himawari as himawari_module
from global_mosaic.acquisition.himawari import download, find_segment_keys


def test_unsupported_satellite_key_returns_empty_list_not_keyerror():
    """A satellite_key outside BUCKETS (e.g. an older Himawari-slot
    occupant coverage.py's date/longitude resolution doesn't know this
    project has no reader for) must return [] like every other "nothing
    found" case here, not raise KeyError."""
    result = find_segment_keys("mtsat-2", dt.datetime(2010, 1, 1))
    assert result == []


class _FakeS3FS:
    def __init__(self):
        self.get_calls: list[tuple[str, str]] = []

    def get(self, key, local_path):
        self.get_calls.append((key, local_path))


def test_find_segment_keys_accounts_for_real_midtime_offset(monkeypatch):
    """Found 2026-08-09: Himawari's actual scan mid-time lands ~5 min
    AFTER its nominal HHMM slot label (against downloaded files: nominal
    0440 -> actual mid 04:45, nominal 0450 -> actual mid 04:55). Sorting
    candidates by distance from `target` to the NOMINAL label alone
    (pre-fix) picks 0450 for target 04:47:44 (nominal gap 2.3 min) over
    0440 (nominal gap 7.7 min) -- backwards, since 0440's actual mid-time
    (04:45) is only 2.7 min from target while 0450's actual mid-time
    (04:55) is 7.3 min away. This test locks in the fix: with both nominal
    slots equally available, the earlier one (0440) must be picked because
    it's the actual nearest."""
    monkeypatch.setattr(himawari_module, "get_s3fs", lambda anonymous=True: object())

    def fake_list_prefix(fs, bucket, prefix):
        # Both 0430/0440/0450/0500 "exist" (10 segments each) -- the
        # test is purely about which one gets TRIED FIRST, i.e. the sort
        # order, not about availability filtering.
        hhmm = prefix.rstrip("/").split("/")[-1]
        return [f"{bucket}/AHI-L1b-FLDK/2026/08/09/{hhmm}/HS_H09_20260809_{hhmm}_B13_FLDK_R20_S0{i}10.DAT.bz2" for i in range(1, 11)]

    monkeypatch.setattr(himawari_module, "list_prefix", fake_list_prefix)

    target = dt.datetime(2026, 8, 9, 4, 47, 44)
    keys = find_segment_keys("himawari-09", target, tolerance_minutes=20)
    picked_nominal = keys[0].split("/")[-2]
    assert picked_nominal == "0440", f"expected the real-nearest slot 0440, got {picked_nominal}"


def test_download_skips_already_cached_segments(tmp_path, monkeypatch):
    """download() used to unconditionally re-fetch every segment from S3
    even when already present locally, unlike meteosat.py/insat.py's
    download()."""
    keys = [f"bucket/AHI-L1b-FLDK/2026/01/01/0010/HS_H09_20260101_0010_B13_FLDK_R20_S0{i}10.DAT.bz2" for i in range(1, 3)]
    monkeypatch.setattr(himawari_module, "find_segment_keys", lambda *a, **k: keys)
    fake_fs = _FakeS3FS()
    monkeypatch.setattr(himawari_module, "get_s3fs", lambda anonymous=True: fake_fs)

    # Pre-create the first segment's local file, leave the second missing.
    first_local_name = keys[0].split("/")[-1]
    (tmp_path / first_local_name).write_bytes(b"already here")

    result = download("himawari-09", dt.datetime(2026, 1, 1, 0, 10), str(tmp_path))

    assert len(result) == 2  # both paths still returned
    fetched_paths = [local for _key, local in fake_fs.get_calls]
    assert not any(first_local_name in p for p in fetched_paths), "cached segment must not be re-fetched"
    assert any(keys[1].split("/")[-1] in p for p in fetched_paths), "missing segment must still be fetched"
