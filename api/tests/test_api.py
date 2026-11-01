import os
import tempfile
import time
from datetime import date, datetime, timezone

_tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=_tmp, ADMIN_KEY="admin-test", JOBS_ENABLED="0")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, jobs, wx  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer admin-test"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def station(client):
    r = client.post("/api/v1/stations", headers=ADMIN, json={
        "slug": "bir-landing", "name": "Bir Landing", "kind": "landing",
        "lat": 32.034, "lon": 76.718, "elevation_m": 1431,
        "launch_dir_from": 160, "launch_dir_to": 250,
    })
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["api_key"]}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_admin_required(client):
    r = client.post("/api/v1/stations", json={"slug": "x1", "name": "X"})
    assert r.status_code == 401
    r = client.post("/api/v1/stations", headers={"Authorization": "Bearer nope"},
                    json={"slug": "x1", "name": "X"})
    assert r.status_code == 403


def test_duplicate_slug_and_rotate(client, station):
    body = {"slug": "bir-landing", "name": "Bir Landing"}
    assert client.post("/api/v1/stations", headers=ADMIN, json=body).status_code == 409
    r = client.post("/api/v1/stations?rotate_key=true", headers=ADMIN, json=body)
    assert r.status_code == 201 and r.json()["rotated"]
    # old key no longer works; put the new one in place for later tests
    assert client.post("/api/v1/ingest", headers=station, json={"temp_c": 1}).status_code == 403
    station["Authorization"] = "Bearer " + r.json()["api_key"]


def test_bands_must_increase(client, station):
    r = client.patch("/api/v1/stations/bir-landing", headers=ADMIN,
                     json={"band_moderate_max_kmh": 5})
    assert r.status_code == 422
    r = client.patch("/api/v1/stations/bir-landing", headers=ADMIN, json={"name": None})
    assert r.status_code == 422


def test_ingest_rejects_bad_key_and_values(client, station):
    assert client.post("/api/v1/ingest", headers={"Authorization": "Bearer x"},
                       json={"temp_c": 10}).status_code == 403
    r = client.post("/api/v1/ingest", headers=station, json={"rh": 140})
    assert r.status_code == 422
    future = iso(time.time() + 3600)
    assert client.post("/api/v1/ingest", headers=station,
                       json={"ts": future, "temp_c": 10}).status_code == 422


def test_ingest_dedupes_and_ignores_unknown_fields(client, station):
    ts = iso(int(time.time()) - 7200)
    body = {"ts": ts, "temp_c": 12.5, "something_else": 1}
    assert client.post("/api/v1/ingest", headers=station, json=body).json()["stored"] == 1
    assert client.post("/api/v1/ingest", headers=station, json=body).json()["stored"] == 0


def test_full_station_summary(client, station):
    now = int(time.time())
    readings = []
    for i in range(30, -1, -1):  # last 30 minutes, wind rising
        t = now - i * 60
        readings.append({
            "ts": iso(t), "temp_c": 20.0, "rh": 50.0, "pressure_pa": 85900,
            "wind_avg_ms": 2.0 if i >= 15 else 5.0, "wind_gust_ms": 6.0, "wind_lull_ms": 1.0,
            "wind_dir_deg": 225.0, "rain_mm": 0.2 if i == 0 else 0.0,
        })
    readings.append({"ts": iso(now - 3 * 3600), "pressure_pa": 86100})
    readings.append({"ts": iso(now - 3600), "temp_c": 17.0})
    r = client.post("/api/v1/ingest", headers=station, json={"readings": readings})
    assert r.status_code == 200 and r.json()["stored"] == len(readings)

    s = client.get("/api/v1/stations/bir-landing").json()["station"]
    d = s["derived"]
    assert s["values"]["wind_avg_ms"]["v"] == 5.0
    assert d["wind_trend"]["trend"] == "rising"
    assert 9.0 < d["dew_point_c"] < 9.5          # 20 °C / 50 % → ~9.3 °C
    assert d["cloud_base_agl_m"] == 1340         # (20 - 9.27) * 125, to 10 m
    assert d["cloud_base_amsl_m"] == 2770
    assert d["pressure_change_3h_hpa"] == -2.0
    assert d["temp_change_1h_c"] == 3.0
    assert d["rain_1h_mm"] == 0.2 and d["raining"] is True
    assert 1005 < d["pressure_sl_hpa"] < 1025
    assert s["launch"] == {"from": 160, "to": 250}
    assert len(s["wind_strip"]["ts"]) >= 30
    assert s["sun"]["sunrise"] < s["sun"]["sunset"]


def test_fly_is_compact(client, station):
    r = client.get("/api/v1/fly")
    body = r.json()
    st = body["stations"][0]
    assert set(st["values"]) <= {"wind_avg_ms", "wind_gust_ms", "wind_lull_ms", "wind_dir_deg", "wind_dir_var_deg"}
    assert st["trend"]["trend"] == "rising"
    assert st["air"]["temp_c"] == 20.0 and st["air"]["rh"] == 50
    assert st["air"]["pressure_change_3h_hpa"] == -2.0
    assert len(r.content) < 4000


def test_series_raw_and_bucketed(client, station):
    raw = client.get("/api/v1/stations/bir-landing/series?minutes=60").json()
    assert raw["step_s"] == 60 and len(raw["ts"]) >= 30
    week = client.get("/api/v1/stations/bir-landing/series?minutes=10080"
                      "&metrics=wind_gust_ms,wind_dir_deg,rain_mm").json()
    assert week["step_s"] == 600
    assert max(v for v in week["series"]["wind_gust_ms"] if v is not None) == 6.0
    assert client.get("/api/v1/stations/bir-landing/series?metrics=api_key_hash").status_code == 422


def test_frames_upload_list_and_prune(client, station):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    old = int(time.time()) - 10 * 86400
    for ts in (old, int(time.time()) - 60):
        r = client.post("/api/v1/stations/bir-landing/frames", params={"ts": iso(ts)}, headers=station,
                        files={"frame": ("f.jpg", jpeg, "image/jpeg")})
        assert r.status_code == 200, r.text
    r = client.post("/api/v1/stations/bir-landing/frames", headers=station,
                    files={"frame": ("f.png", b"\x89PNG....", "image/png")})
    assert r.status_code == 415
    assert len(client.get("/api/v1/stations/bir-landing/frames?minutes=60").json()["frames"]) == 1
    assert jobs.prune_frames(time.time()) == 1
    assert client.get("/api/v1/stations/bir-landing").json()["station"]["frame"] is not None


def test_silent_alert_once(client, station):
    later = time.time() + 3600
    assert len(jobs.check_silent(later)) == 1
    assert jobs.check_silent(later) == []
    assert "again" in jobs.check_silent(time.time())[0]


def test_backup(client):
    path = jobs.backup(time.time())
    assert os.path.getsize(path) > 0


def test_wx_helpers():
    assert wx.circular_mean_deg([350, 10]) in (0.0, 360.0) or abs(wx.circular_mean_deg([350, 10])) < 1e-6
    assert wx.dew_point_c(10, 100) == pytest.approx(10, abs=0.01)
    rise, sset = wx.sun_times(32.034, 76.718, date(2026, 6, 21), "Asia/Kolkata")
    # Bir, midsummer: sunrise ~05:25 IST, sunset ~19:30 IST
    from zoneinfo import ZoneInfo
    r = datetime.fromtimestamp(rise, ZoneInfo("Asia/Kolkata"))
    s = datetime.fromtimestamp(sset, ZoneInfo("Asia/Kolkata"))
    assert (r.hour, s.hour) == (5, 19)
