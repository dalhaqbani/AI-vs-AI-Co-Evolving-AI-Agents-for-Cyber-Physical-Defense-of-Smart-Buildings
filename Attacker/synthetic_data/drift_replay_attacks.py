import pandas as pd


def load_and_prepare(filepath, sensor_name):
    """
    Loads a CASAS file and filters to one sensor, sorted by real timestamp.
    """
    df = pd.read_csv(filepath)
    df = df[df["sensor"] == sensor_name].copy()
    df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


df_temp = load_and_prepare("casas_temperature_balanced.csv", "KitchenATemperature")


def replay_attack(base_timestamp, delays_seconds, action="unlock", source="dashboard"):
    """
    Builds synthetic replay rows by hand, per Deem's moves.py logic.
    """
    rows = []
    rows.append({
        "timestamp": base_timestamp,
        "action": action,
        "source": source,
        "attack_type": "normal"
    })
    for delay in delays_seconds:
        replay_time = base_timestamp + pd.Timedelta(seconds=delay)
        rows.append({
            "timestamp": replay_time,
            "action": action,
            "source": source,
            "attack_type": "replay"
        })
    return pd.DataFrame(rows)


def drift_attack(data, step_per_tick, start_row, num_rows):
    """
    data: the filtered, sorted temperature DataFrame (df_temp)
    step_per_tick: 0.1 to 0.5 degrees C added per row
    """
    out = data.copy()
    out["attack_type"] = "normal"
    for i in range(num_rows):
        row_idx = start_row + i
        if row_idx >= len(out):
            break
        drift_amount = step_per_tick * (i + 1)
        out.loc[row_idx, "state"] += drift_amount
        out.loc[row_idx, "attack_type"] = "drift"
    return out


base = pd.Timestamp("2026-10-16 10:00:00")
replay_samples = replay_attack(
    base_timestamp=base,
    delays_seconds=[5, 57, 600, 5400]  
)
print("Replay samples:")
print(replay_samples)

drift_samples = drift_attack(df_temp, step_per_tick=0.5, start_row=0, num_rows=6)
print("\nDrift samples:")
print(drift_samples[["timestamp", "state", "attack_type"]].head(10))

df_temp_original = load_and_prepare("casas_temperature_balanced.csv", "KitchenATemperature")
comparison = drift_samples[["timestamp", "state", "attack_type"]].head(6).copy()
comparison["original_state"] = df_temp_original["state"].head(6).values
comparison["injected_amount"] = comparison["state"] - comparison["original_state"]
print("\nDrift verification (injected amount should go 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):")
print(comparison)

drift_samples.to_csv("holdout/drift_samples.csv", index=False)
replay_samples.to_csv("holdout/replay_samples.csv", index=False)
print("\nSaved drift_samples.csv and replay_samples.csv to holdout/")