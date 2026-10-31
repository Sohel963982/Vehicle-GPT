"""
VehicleGPT simulator core.

Generates second-by-second vehicle signals (speed, power, battery, thermal,
ADAS) for several simulated days, injects faults described in a YAML file, and
records a ground-truth log of everything it injected.

This file does NOT talk to a CAN bus. It only produces records.
"""
import math
import random
from datetime import datetime, timedelta, timezone

import yaml

# ---------------- vehicle constants ----------------
MASS = 1700.0          # kg
CD = 0.28              # drag coefficient
AREA = 2.3             # frontal area, m^2
CRR = 0.010            # rolling resistance coefficient
RHO = 1.2              # air density, kg/m^3
G = 9.81
ETA_DRIVE = 0.90       # drivetrain efficiency (motoring)
ETA_REGEN = 0.60       # fraction of braking energy recovered
BASE_AUX_KW = 0.4      # always-on electronics
BATTERY_KWH = 60.0
PACK_VOLT = 360.0
R_INT = 0.40           # ohm, effective pack resistance (heat source)
C_THERMAL = 60000.0    # J/K, effective pack heat capacity
K_PUMP = 100.0         # W/K cooling per 100% pump
K_PASSIVE = 5.0        # W/K passive loss to ambient
PARK_TAU = 3 * 3600.0  # s, parked cooling time constant
MAX_ACC = 1.5          # m/s^2
MAX_DEC = 2.0          # m/s^2
TEMP_WARN_C = 50.0     # warning threshold (used for the truth log)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("seed", 1)
    cfg.setdefault("days", 1)
    cfg.setdefault("highway_share", 0.3)
    cfg.setdefault("ambient", {})
    cfg["ambient"].setdefault("base_c", 24.0)
    cfg["ambient"].setdefault("daily_swing_c", 6.0)
    cfg["ambient"].setdefault("trend_c_per_day", 0.0)
    cfg.setdefault("faults", [])
    cfg.setdefault("start_date", None)
    return cfg


class VehicleSim:
    def __init__(self, cfg):
        self.cfg = cfg
        self.rng = random.Random(cfg["seed"])
        self.truth = []          # ground-truth log (list of dicts)
        self._trip_counter = 0
        sd = cfg["start_date"]
        if sd is None:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            start = today - timedelta(days=cfg["days"] - 1)
        else:
            start = datetime.strptime(str(sd), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        self.start_ts = int(start.timestamp())
        self.batt_temp = None
        self.soc = 90.0

    # ---------- helpers ----------
    def _faults(self, ftype, day):
        for f in self.cfg["faults"]:
            if f["type"] != ftype:
                continue
            lo = f.get("from_day", f.get("day", 1))
            hi = f.get("to_day", f.get("day", self.cfg["days"]))
            if lo <= day <= hi:
                yield f

    def _ambient(self, day, ts):
        a = self.cfg["ambient"]
        base = a["base_c"] + a["trend_c_per_day"] * (day - 1)
        hour = (ts % 86400) / 3600.0
        # coldest ~04:00, warmest ~15:00
        return base + a["daily_swing_c"] * math.sin((hour - 9.5) / 24.0 * 2 * math.pi)

    def _highway_share(self, day):
        share = self.cfg["highway_share"]
        for f in self._faults("highway_share_change", day):
            share = f["share"]
        return share

    def _hvac_kw(self, amb, day):
        if amb < 18:
            kw = (18 - amb) * 0.12
        elif amb > 26:
            kw = (amb - 26) * 0.10
        else:
            kw = 0.0
        for f in self._faults("hvac_extra", day):
            kw += f["extra_kw"]
        return min(kw, 6.0)

    # ---------- speed profile ----------
    def _segment_targets(self, kind, seconds):
        """List of target speeds (m/s), one per second, for a city/highway segment."""
        out = []
        while len(out) < seconds:
            if kind == "city":
                v = self.rng.uniform(25, 50) / 3.6
                out += [v] * self.rng.randint(20, 60)
                out += [0.0] * self.rng.randint(5, 20)       # red light / stop
            else:
                v = self.rng.uniform(95, 110) / 3.6
                out += [v] * self.rng.randint(60, 180)
        return out[:seconds]

    # ---------- main generator ----------
    def run(self):
        cfg = self.cfg
        for d in range(1, cfg["days"] + 1):
            day_start = self.start_ts + (d - 1) * 86400
            self.soc = self.rng.uniform(85, 95)       # charged overnight
            share = self._highway_share(d)
            trips = [
                (8 * 3600 + self.rng.randint(-1200, 1200), self.rng.randint(35 * 60, 50 * 60)),
                (18 * 3600 + self.rng.randint(-1800, 1800), self.rng.randint(35 * 60, 50 * 60)),
            ]
            last_end = None
            for ti, (offset, dur) in enumerate(trips):
                yield from self._trip(d, ti == 0, day_start + offset, dur, share, last_end)
                last_end = day_start + offset + dur

    def _trip(self, day, first_trip_of_day, t0, dur, share, last_end):
        self._trip_counter += 1
        trip_id = self._trip_counter
        hw = int(dur * share)
        city = dur - hw
        segs = [("city", city // 2), ("highway", hw), ("city", city - city // 2)]
        targets, kinds = [], []
        for kind, secs in segs:
            targets += self._segment_targets(kind, secs)
            kinds += [kind] * secs

        amb0 = self._ambient(day, t0)
        if self.batt_temp is None:
            self.batt_temp = amb0 + 2.0
        elif last_end is not None:
            gap = t0 - last_end
            self.batt_temp = amb0 + (self.batt_temp - amb0) * math.exp(-gap / PARK_TAU)
        else:  # first trip of a new day, car parked overnight
            self.batt_temp = amb0 + 1.0

        # pump fault for this trip?
        pump_cap, pump_fault_start = None, None
        if first_trip_of_day:
            for f in self._faults("pump_degrade", day):
                pump_cap = f["pump_pct_after"]
                pump_fault_start = f["at_min"] * 60
                self.truth.append({
                    "type": "pump_degrade", "ts": t0 + pump_fault_start, "day": day,
                    "cause": "coolant_pump_degraded", "pump_pct_after": pump_cap,
                })

        lane_rate = 0.5   # disengagements per highway hour, normal
        lane_reason = 2   # glare
        for f in self._faults("lane_fade", day):
            lane_rate = f["rate_per_hour"]
            lane_reason = 1  # faded markings

        v = 0.0
        lka_off_until = -1
        takeover_until = -1
        reason = 0
        crossed = False
        for i in range(dur):
            ts = t0 + i
            ctx = kinds[i]
            amb = self._ambient(day, ts)
            v_target = targets[i]
            dv = v_target - v
            dv = max(-MAX_DEC, min(MAX_ACC, dv))
            v_new = max(0.0, v + dv)
            a = v_new - v
            v = v_new

            # ---- power ----
            f_drag = 0.5 * RHO * CD * AREA * v * v
            f_roll = CRR * MASS * G * (1 + 0.004 * max(0.0, 15 - amb))
            f_acc = MASS * a
            p_wheel = (f_drag + f_roll + f_acc) * v        # W
            if p_wheel >= 0:
                p_batt = p_wheel / ETA_DRIVE
            else:
                p_batt = p_wheel * ETA_REGEN
            hvac = self._hvac_kw(amb, day)
            p_kw = p_batt / 1000.0 + BASE_AUX_KW + hvac

            # ---- SoC ----
            self.soc -= p_kw / 3600.0 / BATTERY_KWH * 100.0
            self.soc = max(0.0, min(100.0, self.soc))

            # ---- thermal ----
            pump = max(20.0, min(100.0, 30.0 + 5.0 * (self.batt_temp - 30.0)))
            if pump_cap is not None and i >= pump_fault_start:
                pump = min(pump, pump_cap)
            current = p_kw * 1000.0 / PACK_VOLT
            heat = current * current * R_INT
            cool = (K_PUMP * pump / 100.0 + K_PASSIVE) * (self.batt_temp - amb)
            self.batt_temp += (heat - cool) / C_THERMAL
            if (not crossed) and self.batt_temp > TEMP_WARN_C:
                crossed = True
                self.truth.append({
                    "type": "battery_overheat_warning", "ts": ts, "day": day,
                    "temp_c": round(self.batt_temp, 2),
                })

            # ---- ADAS ----
            lka_active = 0
            takeover = 0
            if ctx == "highway":
                if ts >= lka_off_until:
                    lka_active = 1
                    reason = 0
                    if self.rng.random() < lane_rate / 3600.0:
                        lka_off_until = ts + 30
                        takeover_until = ts + 5
                        reason = lane_reason if self.rng.random() < 0.9 else 3
                        lka_active = 0
                        self.truth.append({
                            "type": "adas_disengage", "ts": ts, "day": day, "reason": reason,
                        })
                if ts < takeover_until:
                    takeover = 1
            else:
                lka_off_until = -1
                reason = 0

            yield {
                "ts": ts, "trip_id": trip_id, "ctx": ctx, "day": day,
                "Speed_kmh": round(v * 3.6, 2),
                "Battery_SoC": round(self.soc, 2),
                "Power_kW": round(p_kw, 1),
                "Batt_Temp_C": round(self.batt_temp, 2),
                "Coolant_Pump_Pct": round(pump, 2),
                "Ambient_C": round(amb, 2),
                "HVAC_kW": round(hvac, 2),
                "LKA_Active": lka_active,
                "Takeover_Req": takeover,
                "Disengage_Reason": reason if lka_active == 0 and ctx == "highway" else 0,
            }