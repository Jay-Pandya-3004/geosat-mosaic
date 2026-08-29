"""Unit tests for acquisition/insat.py. No real network calls -- every
requests.get/post call is mocked."""

from __future__ import annotations

import datetime as dt

import pytest
import requests

import global_mosaic.acquisition.insat as insat_module
from global_mosaic.acquisition.insat import (
    _download_record,
    _find_local_file,
    _login,
    _real_mid_time,
    _search_dates,
    _search_nearest,
    _shared_login,
    download,
)


def test_find_local_file_ranks_by_estimated_real_mid_time_not_nominal_time(tmp_path):
    """Found via a Tier A run on 2026-08-05: for target
    2026-01-01T00:17:44 with insat-3ds's cached files (nominal 0000,
    actual mid-time 00:14:02; nominal 0030, actual mid-time 00:44:01.5),
    the OLD raw-nominal-time comparison picked the 0030 file (nominal gap
    12:16 vs the 0000 file's nominal gap 17:44) -- then the pipeline's
    downstream mid-time check correctly rejected it (26.3 min away,
    outside the +-5 min window), leaving insat-3ds with NO candidate at
    all for that run, even though the 0000 file's actual mid-time (222s
    gap) was actually a good match. _find_local_file must pick the 0000
    file."""
    (tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5").write_bytes(b"fake")
    (tmp_path / "3SIMG_01JAN2026_0030_L1B_STD_V01R00.h5").write_bytes(b"fake")

    target = dt.datetime(2026, 1, 1, 0, 17, 44)
    result = _find_local_file("insat-3ds", target, str(tmp_path))

    assert result is not None
    assert "0000" in result, f"expected the nominal-0000 file (real mid-time 00:14:02, close to target), got {result}"


def test_find_local_file_picks_the_genuinely_closer_candidate(tmp_path):
    """Sanity check the ranking isn't just always picking the earlier
    file: for a target near the 0030 file's real mid-time (00:44:01.5),
    the 0030 file should win instead."""
    (tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5").write_bytes(b"fake")
    (tmp_path / "3SIMG_01JAN2026_0030_L1B_STD_V01R00.h5").write_bytes(b"fake")

    target = dt.datetime(2026, 1, 1, 0, 44, 0)
    result = _find_local_file("insat-3ds", target, str(tmp_path))

    assert result is not None
    assert "0030" in result, f"expected the nominal-0030 file (real mid-time 00:44:01.5, close to target), got {result}"


def test_find_local_file_returns_none_for_unsupported_satellite_key(tmp_path):
    (tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5").write_bytes(b"fake")
    result = _find_local_file("goes-19", dt.datetime(2026, 1, 1, 0, 0), str(tmp_path))
    assert result is None


def test_find_local_file_returns_none_when_nothing_within_tolerance(tmp_path):
    (tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5").write_bytes(b"fake")
    far_target = dt.datetime(2026, 1, 2, 12, 0)  # more than a day away
    result = _find_local_file("insat-3ds", far_target, str(tmp_path), tolerance_minutes=30.0)
    assert result is None


# ==========================================================
# _real_mid_time / _search_dates
# ==========================================================


def test_real_mid_time_applies_the_generation_offset():
    mid = _real_mid_time("insat-3ds", "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5")
    assert mid == dt.datetime(2026, 1, 1, 0, 14, 1, 740000)


def test_real_mid_time_returns_none_for_unparseable_identifier():
    assert _real_mid_time("insat-3ds", "not_a_real_filename.h5") is None


def test_search_dates_single_day_when_far_from_midnight():
    assert _search_dates(dt.datetime(2026, 1, 1, 12, 0), tolerance_minutes=30.0) == [dt.date(2026, 1, 1)]


def test_search_dates_includes_previous_day_near_midnight():
    dates = _search_dates(dt.datetime(2026, 1, 1, 0, 10), tolerance_minutes=30.0)
    assert dates == [dt.date(2025, 12, 31), dt.date(2026, 1, 1)]


def test_search_dates_includes_next_day_near_midnight():
    dates = _search_dates(dt.datetime(2026, 1, 1, 23, 50), tolerance_minutes=30.0)
    assert dates == [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]


# ==========================================================
# _search_nearest
# ==========================================================


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, headers=None, chunks=None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_search_nearest_picks_closest_entry_by_real_mid_time(monkeypatch):
    """Same bug-avoidance property as _find_local_file's fix, now for a
    live search response: two candidate entries straddle the target, and
    the one closer by ESTIMATED REAL mid-time (not raw nominal filename
    time) must win."""
    entries = [
        {"identifier": "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5", "id": 111},  # real mid ~00:14:02
        {"identifier": "3SIMG_01JAN2026_0030_L1B_STD_V01R00.h5", "id": 222},  # real mid ~00:44:02
    ]

    def fake_get(url, params=None, timeout=None):
        assert params["datasetId"] == "3SIMG_L1B_STD"
        return _FakeResponse(json_data={"entries": entries})

    monkeypatch.setattr(insat_module.requests, "get", fake_get)

    target = dt.datetime(2026, 1, 1, 0, 17, 44)
    result = _search_nearest("insat-3ds", target)

    assert result is not None
    assert result["id"] == 111


def test_search_nearest_returns_none_for_unsupported_satellite(monkeypatch):
    called = []
    monkeypatch.setattr(insat_module.requests, "get", lambda *a, **k: called.append(1))
    assert _search_nearest("goes-19", dt.datetime(2026, 1, 1)) is None
    assert called == []  # never even attempted a search


def test_search_nearest_returns_none_when_nothing_within_tolerance(monkeypatch):
    entries = [{"identifier": "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5", "id": 111}]
    monkeypatch.setattr(insat_module.requests, "get", lambda *a, **k: _FakeResponse(json_data={"entries": entries}))

    far_target = dt.datetime(2026, 1, 2, 12, 0)
    assert _search_nearest("insat-3ds", far_target, tolerance_minutes=30.0) is None


# ==========================================================
# _login
# ==========================================================


def test_login_returns_tokens_on_success(monkeypatch):
    monkeypatch.setattr(
        insat_module.requests,
        "post",
        lambda *a, **k: _FakeResponse(json_data={"access_token": "AT", "refresh_token": "RT"}),
    )
    assert _login("user", "pass") == ("AT", "RT")


def test_login_does_not_retry_on_client_error(monkeypatch):
    """A real 4xx (invalid credentials etc.) shouldn't be retried."""
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _FakeResponse(status_code=401)

    monkeypatch.setattr(insat_module.requests, "post", fake_post)

    with pytest.raises(requests.HTTPError):
        _login("user", "wrong-pass")
    assert len(calls) == 1, "should not retry on an HTTP-level auth failure"


def test_login_retries_on_server_error_then_succeeds(monkeypatch):
    """Found via a live test against the MOSDAC service on 2026-08-05: a
    503 from the login endpoint used to be treated the same as an
    invalid-credentials 401 and never retried, even though it's a
    transient server issue (mirrors a gap in mdapi_opt5.py's own login(),
    whose download_worker() DOES retry 5xx elsewhere -- an inconsistency
    in the reference client itself)."""
    responses = [
        _FakeResponse(status_code=503),
        _FakeResponse(status_code=503),
        _FakeResponse(json_data={"access_token": "AT", "refresh_token": "RT"}),
    ]
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(insat_module.requests, "post", fake_post)
    monkeypatch.setattr(insat_module.time, "sleep", lambda *_: None)  # skip real backoff delay

    assert _login("user", "pass") == ("AT", "RT")
    assert len(calls) == 3


def test_login_retries_on_rate_limit_then_succeeds(monkeypatch):
    """A 429 from the login endpoint used to be treated the same as an
    invalid-credentials 401 and never retried, even though it's the
    transient "back off and try again" response MOSDAC itself sends -- a
    large patch-retry backlog logging in once per stuck candidate could
    trip this rate limit and then permanently give up on that
    band/satellite instead of backing off and retrying, the same mistake
    the 503 case above already made."""
    responses = [
        _FakeResponse(status_code=429),
        _FakeResponse(status_code=429),
        _FakeResponse(json_data={"access_token": "AT", "refresh_token": "RT"}),
    ]
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(insat_module.requests, "post", fake_post)
    monkeypatch.setattr(insat_module.time, "sleep", lambda *_: None)  # skip real backoff delay

    assert _login("user", "pass") == ("AT", "RT")
    assert len(calls) == 3


# ==========================================================
# _shared_login
# ==========================================================


def test_shared_login_reuses_cached_token_within_ttl(tmp_path, monkeypatch):
    """Fixes a real incident: schedule_pipeline.py's _run_partial_recheck
    runs several patch-retry workers concurrently, each a separate
    `python.exe` subprocess -- with a large partial backlog, every one of
    them ended up calling _login for the same MOSDAC account within
    seconds of each other, tripping MOSDAC's own rate limiter on
    /download_api/gettoken even with the 429-retry fix in place (each
    worker's own retry window kept losing the race against the OTHER
    workers' simultaneous attempts). _shared_login caches the token in
    dest_dir so a second call within the TTL window reuses it instead of
    logging in again."""
    login_calls = []
    monkeypatch.setattr(insat_module, "_login", lambda u, p: login_calls.append(1) or ("AT", "RT"))

    first = _shared_login("user", "pass", str(tmp_path))
    second = _shared_login("user", "pass", str(tmp_path))

    assert first == ("AT", "RT")
    assert second == ("AT", "RT")
    assert len(login_calls) == 1, "a second call within the TTL window should reuse the cached token, not log in again"


def test_shared_login_logs_in_again_after_ttl_expires(tmp_path, monkeypatch):
    login_calls = []
    monkeypatch.setattr(insat_module, "_login", lambda u, p: login_calls.append(1) or ("AT", "RT"))
    monkeypatch.setattr(insat_module, "_TOKEN_CACHE_TTL_SECONDS", 0.0)

    _shared_login("user", "pass", str(tmp_path))
    _shared_login("user", "pass", str(tmp_path))

    assert len(login_calls) == 2, "a call after the cached token's TTL has elapsed should log in again"


def test_shared_login_falls_back_to_real_login_on_corrupt_cache(tmp_path, monkeypatch):
    (tmp_path / ".mosdac_token_cache.json").write_text("not valid json")
    monkeypatch.setattr(insat_module, "_login", lambda u, p: ("AT", "RT"))

    assert _shared_login("user", "pass", str(tmp_path)) == ("AT", "RT")


# ==========================================================
# _download_record
# ==========================================================


def test_download_record_succeeds_and_verifies_size(tmp_path, monkeypatch):
    payload = b"0123456789" * 100  # 1000 bytes
    final_path = str(tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5")

    def fake_get(url, headers=None, params=None, stream=None, timeout=None):
        return _FakeResponse(headers={"Content-Length": str(len(payload))}, chunks=[payload])

    monkeypatch.setattr(insat_module.requests, "get", fake_get)

    success, access_token, refresh_token = _download_record("AT", "RT", "user", "pass", 111, final_path, str(tmp_path))

    assert success is True
    assert access_token == "AT"
    with open(final_path, "rb") as f:
        assert f.read() == payload


def test_download_record_refreshes_token_on_401_then_succeeds(tmp_path, monkeypatch):
    payload = b"x" * 500
    final_path = str(tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5")
    call_count = {"n": 0}

    def fake_get(url, headers=None, params=None, stream=None, timeout=None):
        call_count["n"] += 1
        if headers["Authorization"] == "Bearer EXPIRED":
            return _FakeResponse(status_code=401)
        return _FakeResponse(headers={"Content-Length": str(len(payload))}, chunks=[payload])

    monkeypatch.setattr(insat_module.requests, "get", fake_get)
    monkeypatch.setattr(insat_module, "_refresh", lambda username, password, refresh_token, dest_dir: ("FRESH", "RT2"))

    success, access_token, refresh_token = _download_record("EXPIRED", "RT", "user", "pass", 111, final_path, str(tmp_path))

    assert success is True
    assert access_token == "FRESH"
    assert call_count["n"] == 2  # one 401, one real download


def test_download_record_returns_false_on_404(tmp_path, monkeypatch):
    final_path = str(tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5")
    monkeypatch.setattr(insat_module.requests, "get", lambda *a, **k: _FakeResponse(status_code=404))

    success, _, _ = _download_record("AT", "RT", "user", "pass", 111, final_path, str(tmp_path))
    assert success is False


# ==========================================================
# download() -- end-to-end orchestration
# ==========================================================


def test_download_returns_none_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MOSDAC_USERNAME", raising=False)
    monkeypatch.delenv("MOSDAC_PASSWORD", raising=False)
    called = []
    monkeypatch.setattr(insat_module, "_search_nearest", lambda *a, **k: called.append(1))

    result = download("insat-3ds", dt.datetime(2026, 1, 1, 0, 17, 44), str(tmp_path))

    assert result is None
    assert called == [], "must not attempt a live search without credentials"


def test_download_uses_local_file_before_attempting_live_download(tmp_path, monkeypatch):
    (tmp_path / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5").write_bytes(b"fake")
    monkeypatch.setenv("MOSDAC_USERNAME", "user")
    monkeypatch.setenv("MOSDAC_PASSWORD", "pass")
    called = []
    monkeypatch.setattr(insat_module, "_search_nearest", lambda *a, **k: called.append(1))

    result = download("insat-3ds", dt.datetime(2026, 1, 1, 0, 17, 44), str(tmp_path))

    assert result is not None
    assert called == [], "a good-enough local file must win over a live download attempt"


def test_download_orchestrates_search_login_and_download(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSDAC_USERNAME", "user")
    monkeypatch.setenv("MOSDAC_PASSWORD", "pass")

    entry = {"identifier": "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5", "id": 111}
    monkeypatch.setattr(insat_module, "_search_nearest", lambda *a, **k: entry)
    monkeypatch.setattr(insat_module, "_login", lambda username, password: ("AT", "RT"))

    download_calls = []

    def fake_download_record(access_token, refresh_token, username, password, record_id, final_path, dest_dir):
        download_calls.append((record_id, final_path))
        with open(final_path, "wb") as f:
            f.write(b"downloaded")
        return True, access_token, refresh_token

    monkeypatch.setattr(insat_module, "_download_record", fake_download_record)

    result = download("insat-3ds", dt.datetime(2026, 1, 1, 0, 17, 44), str(tmp_path))

    assert result == str(tmp_path / entry["identifier"])
    assert download_calls == [(111, result)]
