import argparse
import json
from pathlib import Path

from attack_injection import bias_attack, cmd_inject_attack, load_casas
from attack_injection import spoof_attack, stuck_at_attack

# moves.py and the real DHT22 send temperature and humidity together, so the bias and
# spoof files carry a humidity column. 48.0 is the same placeholder moves.py falls back to.
HUMIDITY_PLACEHOLDER = 48.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--temperature", required=True)
    parser.add_argument("--motion", required=True)
    parser.add_argument("--door", required=True)
    parser.add_argument("--temperature-sensor", required=True)
    parser.add_argument("--motion-sensor", required=True)
    parser.add_argument("--door-sensor", required=True)
    parser.add_argument("--home-id")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--offset", type=float, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="mariam_samples")
    args = parser.parse_args()

    if args.count < 2:
        parser.error("count must be at least two for frozen attacks.")
    if not 0 < args.offset <= 10:
        parser.error("offset must be greater than 0 and at most 10 C.")

    temperature = load_casas(args.temperature, args.temperature_sensor, args.home_id)
    motion = load_casas(args.motion, args.motion_sensor, args.home_id)
    door = load_casas(args.door, args.door_sensor, args.home_id)
    if args.start < 0 or args.start + args.count > min(len(temperature), len(motion), len(door)):
        parser.error("The selected streams must contain the requested attack window.")

    motion_value = "OFF" if motion.loc[args.start, "state"] == "ON" else "ON"
    start, count = args.start, args.count
    results = {
        "bias_positive": (bias_attack(temperature, args.offset, start, count),
                          {"target": "temperature", "offset_c": args.offset}),
        "bias_negative": (bias_attack(temperature, -args.offset, start, count),
                          {"target": "temperature", "offset_c": -args.offset}),
        "motion_frozen": (stuck_at_attack(motion, "motion", motion_value, "frozen", start, count),
                          {"target": "motion", "value": motion_value}),
        "motion_single": (stuck_at_attack(motion, "motion", motion_value, "single", start),
                          {"target": "motion", "value": motion_value}),
        "spoof": (spoof_attack(temperature, start, count, args.seed),
                  {"target": "temperature", "range_c": [10, 50], "seed": args.seed}),
        "inject_unlock": (cmd_inject_attack(door, "lock", "unlock", start),
                          {"target": "lock_1", "action": "unlock"}),
        "inject_lock": (cmd_inject_attack(door, "lock", "lock", start),
                        {"target": "lock_1", "action": "lock"}),
        "inject_safe_mode": (cmd_inject_attack(motion, "fan", "safe_mode", start),
                             {"target": "fan_1", "action": "safe_mode"}),
    }

    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        parser.error("Choose an empty output folder to keep earlier samples.")
    output.mkdir(parents=True, exist_ok=True)
    samples = {}
    for name, (data, settings) in results.items():
        if name.startswith("motion"):
            # Mariam's pipeline, moves.py and the backend read motion as true/false.
            data["state"] = data["state"].map({"ON": True, "OFF": False}).astype(bool)
            settings = {**settings, "value": settings["value"] == "ON"}
        if name.startswith(("bias", "spoof")):
            data["humidity"] = HUMIDITY_PLACEHOLDER
        data.to_csv(output / (name + ".csv"), index=False)
        attacked = data.loc[data["attack_type"] != "normal"]
        samples[name] = {
            "parameters": settings,
            "attacked_rows": len(attacked),
            "examples": json.loads(attacked.head(3).to_json(orient="records", date_format="iso")),
        }
    (output / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print("Saved 8 CSV files and samples.json in", output)


if __name__ == "__main__":
    main()
