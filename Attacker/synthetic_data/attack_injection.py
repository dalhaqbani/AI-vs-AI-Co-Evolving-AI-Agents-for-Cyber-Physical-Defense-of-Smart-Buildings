import json
import math
import random

import pandas as pd


def load_casas(file_path, sensor, home_id=None):
    data = pd.read_csv(file_path, dtype={"home_id": str})
    required = {"sensor", "state", "date", "time"}
    if not required.issubset(data.columns):
        raise ValueError("The CSV must contain sensor, state, date and time.")

    data = data.loc[data["sensor"] == sensor].copy()
    if home_id is not None:
        if "home_id" not in data.columns:
            raise ValueError("This CSV has no home_id column.")
        data = data.loc[data["home_id"] == str(home_id)].copy()
    return prepare_data(data)


def prepare_data(data):
    result = data.copy(deep=True)
    if result.empty:
        raise ValueError("No matching sensor readings were found.")
    if result["sensor"].nunique(dropna=False) != 1:
        raise ValueError("Select one sensor before applying an attack.")
    if "home_id" in result and result["home_id"].nunique(dropna=False) != 1:
        raise ValueError("Select one home_id before applying an attack.")
    if "attack_type" in result and not result["attack_type"].eq("normal").all():
        raise ValueError("Use the original readings for each attack.")

    times = result["date"].astype(str) + " " + result["time"].astype(str)
    result["timestamp"] = pd.to_datetime(times, format="mixed", errors="raise")
    if result["timestamp"].isna().any():
        raise ValueError("All readings must have a valid timestamp.")
    result = result.sort_values("timestamp", kind="stable").reset_index(drop=True)
    result["attack_type"] = "normal"
    return result


def attack_window(data, start, count):
    if count is None:
        count = len(data) - start
    if not isinstance(start, int) or not isinstance(count, int):
        raise ValueError("start and count must be integers.")
    if start < 0 or count < 1 or start + count > len(data):
        raise ValueError("The attack window is outside the selected readings.")
    return list(range(start, start + count))


def bias_attack(data, offset, start=0, count=None):
    offset = float(offset)
    if not math.isfinite(offset) or offset == 0 or abs(offset) > 10:
        raise ValueError("The offset must be nonzero and between -10 and 10 C.")

    result = prepare_data(data)
    values = pd.to_numeric(result["state"], errors="raise")
    if not values.map(math.isfinite).all():
        raise ValueError("Temperature readings must be finite numbers.")
    selected = attack_window(result, start, count)
    result["state"] = values.astype(float)
    result.loc[selected, "state"] = values.loc[selected] + offset
    result.loc[selected, "attack_type"] = "bias"
    return result


def stuck_at_attack(data, component, value, mode="frozen", start=0, count=None):
    if component not in {"motion", "lock", "motion_1", "lock_1"}:
        raise ValueError("The component must be motion or lock.")
    if mode not in {"frozen", "single"}:
        raise ValueError("The mode must be frozen or single.")
    if value not in {"ON", "OFF"}:
        raise ValueError("Use ON or OFF for the cleaned CASAS state.")
    if mode == "single":
        if count not in (None, 1):
            raise ValueError("A single attack changes exactly one row.")
        count = 1

    result = prepare_data(data)
    if not result["state"].isin(["ON", "OFF"]).all():
        raise ValueError("Motion and door readings must use ON and OFF.")
    selected = attack_window(result, start, count)
    if mode == "frozen" and len(selected) < 2:
        raise ValueError("A frozen attack needs at least two consecutive rows.")
    if result.loc[selected, "state"].eq(value).all():
        raise ValueError("Choose a value that changes at least one reading.")

    result.loc[selected, "state"] = value
    result.loc[selected, "attack_type"] = "stuck_at_" + mode
    return result


def spoof_attack(data, start=0, count=None, seed=42):
    result = prepare_data(data)
    values = pd.to_numeric(result["state"], errors="raise")
    if not values.map(math.isfinite).all():
        raise ValueError("Temperature readings must be finite numbers.")
    selected = attack_window(result, start, count)
    generator = random.Random(seed)
    result["state"] = values.astype(float)

    for index in selected:
        value = round(generator.uniform(10, 50), 1)
        while value == values.loc[index]:
            value = round(generator.uniform(10, 50), 1)
        result.loc[index, "state"] = value
    result.loc[selected, "attack_type"] = "spoof"
    return result


def cmd_inject_attack(data, component, action, start=0, count=1, source="dashboard"):
    components = {"lock": "lock_1", "fan": "fan_1", "lock_1": "lock_1", "fan_1": "fan_1"}
    component_id = components.get(component)
    allowed = {"lock_1": {"lock", "unlock"}, "fan_1": {"safe_mode"}}
    if component_id not in allowed or action not in allowed[component_id]:
        raise ValueError("Use lock/unlock for the lock or safe_mode for the fan.")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("The command source must be a nonempty string.")

    baseline = prepare_data(data)
    selected = attack_window(baseline, start, count)
    payload = json.dumps({"action": action, "source": source})
    commands = pd.DataFrame({
        "timestamp": baseline.loc[selected, "timestamp"].to_list(),
        "component_id": component_id,
        "topic": "building/" + component_id + "/commands",
        "action": action,
        "source": source,
        "payload": payload,
        "attack_type": "cmd_inject",
    })
    return commands
