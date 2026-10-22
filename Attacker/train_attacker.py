"""Train the PPO attacker against the surrogate (Week 5 pipeline check).

    pip install stable-baselines3
    python attacker/train_attacker.py --timesteps 50000

Optionally warm-start from a public-dataset move prior first (see
compute_move_prior.py and pretrain_policy.py), per the charter's "warm-started
by pretraining on public attack datasets, then improved by reinforcement
learning":

    python attacker/train_attacker.py --timesteps 200000 \\
        --pretrain-prior attacker/move_prior_ciciot.json attacker/move_prior_tonio.json

Compares the trained policy with a random policy and a do-nothing policy on the
same seeds, and prints which moves the trained agent uses. The defender and the
harm metric are PLACEHOLDERS (pipeline.py), so treat the numbers as proof that
the loop runs, not as results.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attacker_env import AttackerEnv  # noqa: E402


def evaluate(env: AttackerEnv, policy, episodes: int, seed0: int = 1000):
    returns, moves = [], Counter()
    for ep in range(episodes):
        obs, _ = env.reset(seed=seed0 + ep)
        done, total = False, 0.0
        while not done:
            obs, r, terminated, truncated, info = env.step(policy(obs))
            total += r
            moves[info["move"]] += 1
            done = terminated or truncated
        returns.append(total)
    return float(np.mean(returns)), float(np.std(returns)), moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timesteps", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eval-episodes", type=int, default=20)
    ap.add_argument("--save", default="attacker_ppo")
    ap.add_argument("--pretrain-prior", nargs="+", default=None,
                     help="one or more move_prior.json files from compute_move_prior.py; "
                          "warm-starts the policy's move choice before RL training")
    ap.add_argument("--pretrain-steps", type=int, default=4000)
    ap.add_argument("--pretrain-epochs", type=int, default=4)
    args = ap.parse_args()

    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor

    env = Monitor(AttackerEnv())
    model = PPO("MlpPolicy", env, seed=args.seed, verbose=0, n_steps=1024,
                batch_size=128, learning_rate=3e-4, ent_coef=0.01)

    if args.pretrain_prior:
        from pretrain_policy import load_and_merge_priors, warm_start_from_prior
        prior = load_and_merge_priors(args.pretrain_prior)
        print("Warm-starting from public-dataset prior before RL training...")
        warm_start_from_prior(model, AttackerEnv(), prior,
                               collect_steps=args.pretrain_steps, epochs=args.pretrain_epochs)
        print()

    model.learn(total_timesteps=args.timesteps)
    model.save(args.save)

    eval_env = AttackerEnv()
    rng = np.random.default_rng(args.seed)
    rows = {
        "trained PPO": evaluate(eval_env, lambda o: model.predict(o, deterministic=True)[0],
                                args.eval_episodes),
        "random": evaluate(eval_env, lambda o: eval_env.action_space.sample(), args.eval_episodes),
        "do nothing": evaluate(eval_env, lambda o: np.array([0, 0, 0]), args.eval_episodes),
    }
    print(f"\nEpisode return over {args.eval_episodes} fixed seeds (300 ticks each)")
    for name, (mean, std, moves) in rows.items():
        print(f"  {name:12s} {mean:8.1f} +/- {std:5.1f}")
    print("\nMoves used by the trained agent:")
    total = sum(rows["trained PPO"][2].values())
    for move, n in rows["trained PPO"][2].most_common():
        print(f"  {move:14s} {100 * n / total:5.1f}%")


if __name__ == "__main__":
    main()