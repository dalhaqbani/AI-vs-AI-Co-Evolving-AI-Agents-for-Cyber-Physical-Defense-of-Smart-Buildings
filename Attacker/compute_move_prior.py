"""Turn a cleaned public attack dataset into a move-selection prior for warm-start.

The charter says the PPO policy "is warm-started by pretraining on public attack
datasets". Those datasets (CICIoT2023, TON_IoT) are network-level attack FLOWS,
not sequences of our 6 moves, so there's no row-to-move replay to imitate.
What we CAN pull out is honest: how often each broad attack family shows up.
That becomes a prior distribution over our moves, used to bias the policy's
move-selection before RL training starts (see pretrain_policy.py), so the
agent doesn't spend its first thousands of steps fixated on one move before
discovering the others -- the exact failure mode seen in early training runs.

Usage:
    python attacker/compute_move_prior.py --csv path/to/ciciot2023_filtered_clean.csv --label-col label
    python attacker/compute_move_prior.py --csv path/to/ton_iot/*.csv --label-col type --out attacker/move_prior_tonio.json

TON_IoT ships as several per-device files (Garage Door, Thermostat, Motion
Light, Fridge, ...) rather than one CSV -- pass them all to --csv together
(several paths, or a shell glob like ton_iot/*.csv) and they're combined into
one prior, as long as they all share the same label column name.

Run it once per cleaned dataset, then merge/average the resulting JSON files by
hand (or pass --merge) before pretraining. PRINTS every label it found and
which move (if any) it mapped to -- check this table once; the substring
matching below is a best guess and may need a line added for a label spelled
differently in your actual file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moves import MOVES  # noqa: E402

# Each of our moves, as a list of lowercase substrings that identify it in a
# public dataset's label column. A label matching none of these, and not
# "benign"/"normal", is left unmapped (it's a real attack family we don't
# model, e.g. flooding -- deliberately out of scope per the charter).
MOVE_KEYWORDS: dict[str, list[str]] = {
    "temp_spoof":    ["spoof", "fake", "false_data"],
    "const_bias":    [],          # no public dataset distinguishes bias from spoofing
    "gradual_drift": [],          # same -- see note below
    "pir_freeze":    ["stuck", "freeze", "replay_sensor"],
    "lock_replay":   ["replay"],
    "cmd_inject":    ["inject", "command", "unauthorized", "mitm", "backdoor"],
}
BENIGN_KEYWORDS = ["benign", "normal", "legit"]
# Explicitly NOT mapped, on purpose: flood/ddos/dos/mirai/recon/scan/bruteforce/
# password/ransomware/xss/web -- these are real categories in CICIoT2023/TON_IoT
# but outside this project's attack set (infrastructure flooding is out of scope
# per the charter; the others don't correspond to any of our 6 moves).


def classify(label: str) -> str | None:
    low = label.lower()
    if any(k in low for k in BENIGN_KEYWORDS):
        return "noop"
    for move, keywords in MOVE_KEYWORDS.items():
        if any(k in low for k in keywords):
            return move
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, nargs="+",
                     help="one or more cleaned dataset CSVs -- pass all of them together "
                          "for a dataset split into multiple files (e.g. TON_IoT's "
                          "per-device files), and they're combined into one prior")
    ap.add_argument("--label-col", required=True,
                     help="name of the attack-label column (must be the same in every file passed)")
    ap.add_argument("--out", default="attacker/move_prior.json")
    args = ap.parse_args()

    import csv as csv_module
    counts: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    total = 0
    for csv_path in args.csv:
        file_total = 0
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv_module.DictReader(f)
            if args.label_col not in (reader.fieldnames or []):
                print(f"{csv_path}: column {args.label_col!r} not found. "
                      f"Columns are: {reader.fieldnames}")
                print("Fix --label-col (it may differ per file) and rerun just that file.")
                return
            for row in reader:
                label = (row.get(args.label_col) or "").strip()
                if not label:
                    continue
                total += 1
                file_total += 1
                move = classify(label)
                if move is None:
                    unmapped[label] += 1
                else:
                    counts[move] += 1
        print(f"  read {file_total:8d} labeled rows from {csv_path}")

    print(f"\n{total} labeled rows read from {len(args.csv)} file(s)\n")
    print("Mapped to a move:")
    for move in MOVES:
        n = counts.get(move, 0)
        pct = 100 * n / total if total else 0
        print(f"  {move:14s} {n:8d}  ({pct:5.1f}%)")

    print("\nNOT mapped to any move (real attack families outside our 6 moves,")
    print("e.g. flooding -- expected, not an error):")
    for label, n in unmapped.most_common(15):
        print(f"  {label:30s} {n:8d}")
    if len(unmapped) > 15:
        print(f"  ... and {len(unmapped) - 15} more distinct labels")

    mapped_total = sum(counts.values())
    if mapped_total == 0:
        print("\nNothing mapped -- check --label-col and the keyword lists in this file "
              "against the labels printed above, then rerun.")
        return

    prior = {move: counts.get(move, 0) / mapped_total for move in MOVES}
    Path(args.out).write_text(json.dumps(prior, indent=2))
    print(f"\nWrote prior (over the {mapped_total} mapped rows, {len(MOVES)} moves) to {args.out}")
    print("Review the unmapped-label table above before trusting this -- if a label there")
    print("is actually one of our attacks spelled differently, add it to MOVE_KEYWORDS and rerun.")


if __name__ == "__main__":
    main()
