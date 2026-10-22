import argparse
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


def profile_csv(source, name, label_column, benign_labels, unknown_labels=None,
                missing_tokens=None, chunk_size=100000, infer_types=False):
    unknown_labels = {"unknown", "unlabelled", "unlabeled"} if unknown_labels is None else unknown_labels
    rows = 0
    missing = Counter()
    labels = Counter()
    mqtt_rows = 0
    mqtt_available = False
    columns = None
    missing_tokens = ["", "NA", "N/A", "NaN", "nan", "NULL", "null", "?"] if missing_tokens is None else missing_tokens
    types = {label_column: str} if label_column is not None else None
    for chunk in pd.read_csv(source, chunksize=chunk_size, dtype=types if infer_types else str,
                             keep_default_na=False, na_values=missing_tokens, low_memory=False):
        current_columns = list(chunk.columns)
        if columns is None:
            columns = current_columns
        if label_column is not None and label_column not in chunk.columns:
            raise ValueError(f"{name}: label column {label_column!r} was not found")
        rows += len(chunk)
        missing.update(chunk.isna().sum().to_dict())
        if label_column is not None:
            values = chunk[label_column].dropna().str.strip()
            labels.update(values[values.ne("")].value_counts().to_dict())
        if "frame.protocols" in chunk:
            mqtt_available = True
            mqtt_rows += int(chunk["frame.protocols"].str.contains("mqtt", case=False, na=False).sum())
    if columns is None:
        raise ValueError(f"{name}: no data chunks were read")
    benign = sum(count for label, count in labels.items() if label.casefold() in benign_labels)
    labelled = sum(labels.values())
    unknown = sum(count for label, count in labels.items() if label.casefold() in unknown_labels)
    attack = labelled - benign - unknown
    return {
        "file": name, "rows": rows, "column_count": len(columns), "columns": columns,
        "classification_available": label_column is not None,
        "missing_by_column": dict(missing), "missing_cells": sum(missing.values()),
        "label_counts": dict(labels), "benign_rows": benign, "attack_rows": attack,
        "unknown_rows": unknown, "unlabelled_rows": rows - labelled,
        "benign_to_attack_ratio": benign / attack if attack else None,
        "benign_percent_of_labelled": 100 * benign / labelled if labelled else None,
        "attack_percent_of_labelled": 100 * attack / labelled if labelled else None,
        "mqtt_rows": mqtt_rows if mqtt_available else None,
    }


def combine(profiles, dataset, label_column, benign_labels, unknown_labels=None,
            missing_tokens=None):
    unknown_labels = {"unknown", "unlabelled", "unlabeled"} if unknown_labels is None else unknown_labels
    schemas = {frozenset(item["columns"]) for item in profiles}
    if len(schemas) != 1:
        raise ValueError("Input files have different schemas; profile each data source separately")
    labels = Counter()
    missing = Counter()
    for item in profiles:
        labels.update(item["label_counts"])
        missing.update(item["missing_by_column"])
    benign = sum(item["benign_rows"] for item in profiles)
    attack = sum(item["attack_rows"] for item in profiles)
    unknown = sum(item["unknown_rows"] for item in profiles)
    labelled = benign + attack + unknown
    return {
        "dataset": dataset, "status": "measured", "files_profiled": len(profiles),
        "row_count": sum(item["rows"] for item in profiles),
        "column_count": len(profiles[0]["columns"]), "columns": profiles[0]["columns"],
        "column_order_variants": len({tuple(item["columns"]) for item in profiles}),
        "label_column": label_column, "benign_labels": sorted(benign_labels),
        "classification_available": label_column is not None,
        "unknown_labels": sorted(unknown_labels), "unknown_rows": unknown,
        "missing_definition": "Missing tokens: " + repr(missing_tokens if missing_tokens is not None else ["", "NA", "N/A", "NaN", "nan", "NULL", "null", "?"]) + "; no rows dropped",
        "missing_cells": sum(missing.values()), "missing_by_column": dict(missing),
        "label_counts": dict(labels), "benign_rows": benign, "attack_rows": attack,
        "unlabelled_rows": sum(item["unlabelled_rows"] for item in profiles),
        "benign_to_attack_ratio": benign / attack if attack else None,
        "benign_percent_of_labelled": 100 * benign / labelled if labelled else None,
        "attack_percent_of_labelled": 100 * attack / labelled if labelled else None,
        "mqtt_rows": sum(item["mqtt_rows"] for item in profiles) if all(item["mqtt_rows"] is not None for item in profiles) else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--label", default="label")
    parser.add_argument("--benign", nargs="+", default=["Benign", "Normal"])
    parser.add_argument("--unknown", nargs="*", default=["Unknown", "Unlabelled", "Unlabeled"])
    parser.add_argument("--missing-token", action="append", default=[])
    parser.add_argument("--chunk-size", type=int, default=100000)
    parser.add_argument("--infer-types", action="store_true")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.input)
    if args.label.casefold() == "none":
        args.label = None
    if args.chunk_size < 1:
        raise ValueError("chunk-size must be positive")
    missing_tokens = ["", "NA", "N/A", "NaN", "nan", "NULL", "null", "?"] + args.missing_token
    benign_labels = {value.casefold() for value in args.benign}
    unknown_labels = {value.casefold() for value in args.unknown}
    if benign_labels & unknown_labels:
        raise ValueError("Benign and unknown labels must be different")
    profiles = []
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            names = sorted(name for name in archive.namelist() if name.startswith(args.prefix) and name.lower().endswith(".csv"))
            for name in names:
                with archive.open(name) as stream:
                    profiles.append(profile_csv(stream, name, args.label, benign_labels, unknown_labels, missing_tokens, args.chunk_size, args.infer_types))
                print(f"Profiled {len(profiles)}/{len(names)}: {name}", flush=True)
    else:
        paths = sorted(path.rglob("*.csv")) if path.is_dir() else [path]
        for item in paths:
            profiles.append(profile_csv(item, str(item), args.label, benign_labels, unknown_labels, missing_tokens, args.chunk_size, args.infer_types))
    if not profiles:
        raise ValueError("No matching CSV files were found")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "file_profiles.json").write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    result = combine(profiles, args.dataset, args.label, benign_labels, unknown_labels, missing_tokens)
    (output / "profile.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    pd.DataFrame([{key: value for key, value in item.items() if key not in {"columns", "missing_by_column", "label_counts"}} for item in profiles]).to_csv(output / "files.csv", index=False)
    pd.DataFrame([{"file": item["file"], "column": column, "missing": count} for item in profiles for column, count in item["missing_by_column"].items()]).to_csv(output / "missing_values.csv", index=False)
    pd.DataFrame([{"label": label, "rows": count} for label, count in result["label_counts"].items()]).to_csv(output / "labels.csv", index=False)


if __name__ == "__main__":
    main()
