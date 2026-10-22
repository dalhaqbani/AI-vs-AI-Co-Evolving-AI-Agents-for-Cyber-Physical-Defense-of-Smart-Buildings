"""Sprint 2 verification: does the placeholder defender ever catch pir_spoof?

Loads the model saved by train_attacker.py (attacker_ppo.zip) and runs it for
several episodes, tallying how often each move was used and how often each
use was flagged by the placeholder defender (info["detected"]).

Usage (from repo root):
    python attacker/verify_detection.py
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attacker_env import AttackerEnv  # noqa: E402


def main() -> None:
    from stable_baselines3 import PPO

    model = PPO.load("attacker_ppo")
    env = AttackerEnv()

    used = Counter()
    detected = defaultdict(int)
    harm_total = defaultdict(float)

    episodes = 20
    for ep in range(episodes):
        obs, _ = env.reset(seed=1000 + ep)
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, r, terminated, truncated, info = env.step(action)
            move = info["move"]
            used[move] += 1
            detected[move] += info["detected"]
            harm_total[move] += info["harm"]
            done = terminated or truncated

    print(f"Over {episodes} episodes (300 ticks each), trained policy deterministic:\n")
    print(f"{'move':14s} {'used':>8s} {'detected':>10s} {'detect %':>10s} {'harm sum':>10s}")
    for move, n in used.most_common():
        d = detected[move]
        pct = 100 * d / n if n else 0.0
        print(f"{move:14s} {n:8d} {d:10d} {pct:9.1f}% {harm_total[move]:10.2f}")


if __name__ == "__main__":
    main()