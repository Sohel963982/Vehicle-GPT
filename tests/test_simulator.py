import collections
from pathlib import Path

import cantools

from sim.simulator import VehicleSim, load_config

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "sim" / "scenarios"


def run(cfg):
    s = VehicleSim(cfg)
    return list(s.run()), s


def wh_per_km(recs):
    wh = sum(r["Power_kW"] * 1000 / 3600 for r in recs)
    km = sum(r["Speed_kmh"] / 3600 for r in recs)
    return wh / km


def small_cfg(**over):
    cfg = load_config(str(SC / "demo_14days.yaml"))
    cfg["days"] = 3
    cfg["faults"] = []
    cfg["start_date"] = "2026-01-01"
    cfg.update(over)
    return cfg


def test_same_seed_gives_same_data():
    a, _ = run(small_cfg())
    b, _ = run(small_cfg())
    assert a[:500] == b[:500]
    assert len(a) == len(b)


def test_records_match_dbc_signals():
    db = cantools.database.load_file(str(ROOT / "dbc" / "vehicle.dbc"))
    names = {sig.name for m in db.messages for sig in m.signals}
    recs, _ = run(small_cfg(days=1))
    for n in names:
        assert n in recs[0], f"simulator does not produce {n}"


def test_values_are_physically_sane():
    recs, _ = run(small_cfg())
    assert all(0 <= r["Speed_kmh"] <= 130 for r in recs)
    assert all(0 <= r["Battery_SoC"] <= 100 for r in recs)
    assert all(-40 <= r["Batt_Temp_C"] <= 120 for r in recs)
    assert min(r["Power_kW"] for r in recs) < 0, "no regenerative braking seen"
    trip1 = [r for r in recs if r["trip_id"] == 1]
    assert trip1[-1]["Battery_SoC"] < trip1[0]["Battery_SoC"]


def test_values_fit_dbc_ranges():
    db = cantools.database.load_file(str(ROOT / "dbc" / "vehicle.dbc"))
    recs, _ = run(small_cfg())
    for m in db.messages:
        for sig in m.signals:
            lo, hi = sig.minimum, sig.maximum
            vals = [r[sig.name] for r in recs]
            assert min(vals) >= lo and max(vals) <= hi, sig.name


def test_highway_uses_more_energy_per_km_than_city():
    recs, _ = run(small_cfg())
    city = [r for r in recs if r["ctx"] == "city"]
    hwy = [r for r in recs if r["ctx"] == "highway"]
    assert wh_per_km(hwy) > wh_per_km(city)


def test_colder_weather_increases_energy_use():
    warm, _ = run(small_cfg(ambient={"base_c": 25, "daily_swing_c": 0, "trend_c_per_day": 0}))
    cold, _ = run(small_cfg(ambient={"base_c": 0, "daily_swing_c": 0, "trend_c_per_day": 0}))
    assert wh_per_km(cold) > wh_per_km(warm) * 1.10


def test_overheat_scenario_truth_log():
    cfg = load_config(str(SC / "overheat.yaml"))
    recs, sim = run(cfg)
    kinds = {t["type"]: t for t in sim.truth}
    assert "pump_degrade" in kinds
    assert "battery_overheat_warning" in kinds
    fault_ts = kinds["pump_degrade"]["ts"]
    warn_ts = kinds["battery_overheat_warning"]["ts"]
    assert fault_ts < warn_ts < fault_ts + 40 * 60
    # the battery was NOT already above the threshold before the fault
    before = [r for r in recs if r["ts"] < fault_ts]
    assert max(r["Batt_Temp_C"] for r in before) < 50


def test_pump_really_drops_after_fault():
    cfg = load_config(str(SC / "overheat.yaml"))
    recs, sim = run(cfg)
    fault_ts = [t for t in sim.truth if t["type"] == "pump_degrade"][0]["ts"]
    before = [r["Coolant_Pump_Pct"] for r in recs if fault_ts - 120 <= r["ts"] < fault_ts]
    after = [r["Coolant_Pump_Pct"] for r in recs if fault_ts <= r["ts"] < fault_ts + 120]
    assert sum(after) / len(after) < sum(before) / len(before) - 15


def test_lane_fade_causes_more_disengagements():
    base = small_cfg(days=10)
    faded = small_cfg(days=10)
    faded["faults"] = [{"type": "lane_fade", "from_day": 1, "to_day": 10, "rate_per_hour": 20}]
    _, s1 = run(base)
    _, s2 = run(faded)
    n1 = sum(1 for t in s1.truth if t["type"] == "adas_disengage")
    n2 = sum(1 for t in s2.truth if t["type"] == "adas_disengage")
    assert n2 > n1 + 5


def test_adas_takeover_follows_disengage():
    faded = small_cfg(days=10)
    faded["faults"] = [{"type": "lane_fade", "from_day": 1, "to_day": 10, "rate_per_hour": 20}]
    recs, s = run(faded)
    ev = [t for t in s.truth if t["type"] == "adas_disengage"][0]
    at = {r["ts"]: r for r in recs}
    assert at[ev["ts"]]["LKA_Active"] == 0
    assert at[ev["ts"]]["Takeover_Req"] == 1
    assert at[ev["ts"]]["Disengage_Reason"] == ev["reason"]