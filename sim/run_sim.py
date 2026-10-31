"""
Run a scenario and save the result.

  python -m sim.run_sim --scenario sim/scenarios/overheat.yaml
  python -m sim.run_sim --scenario sim/scenarios/demo_14days.yaml

Creates:
  data/<name>.jsonl        one JSON record per simulated second
  data/<name>_truth.json   the faults the simulator injected (ground truth)
"""
import argparse
import collections
import json
from pathlib import Path

from sim.simulator import VehicleSim, load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    cfg = load_config(args.scenario)
    name = cfg.get("name", Path(args.scenario).stem)
    sim = VehicleSim(cfg)

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / f"{name}.jsonl"

    n = 0
    wh = collections.defaultdict(float)
    km = collections.defaultdict(float)
    max_temp = 0.0
    with open(jsonl, "w", encoding="utf-8") as f:
        for rec in sim.run():
            f.write(json.dumps(rec) + "\n")
            n += 1
            wh[rec["ctx"]] += rec["Power_kW"] * 1000.0 / 3600.0
            km[rec["ctx"]] += rec["Speed_kmh"] / 3600.0
            max_temp = max(max_temp, rec["Batt_Temp_C"])

    truth_path = out / f"{name}_truth.json"
    with open(truth_path, "w", encoding="utf-8") as f:
        json.dump(sim.truth, f, indent=2)

    print(f"Scenario        : {name}")
    print(f"Records written : {n} ({n / 3600:.1f} driving hours) -> {jsonl}")
    print(f"Truth events    : {len(sim.truth)} -> {truth_path}")
    counts = collections.Counter(t["type"] for t in sim.truth)
    for k, v in counts.items():
        print(f"   {k}: {v}")
    print(f"Max battery temp: {max_temp:.1f} C")
    for ctx in ("city", "highway"):
        if km[ctx] > 0:
            print(f"Energy {ctx:7s}: {wh[ctx] / km[ctx]:.0f} Wh/km")


if __name__ == "__main__":
    main()