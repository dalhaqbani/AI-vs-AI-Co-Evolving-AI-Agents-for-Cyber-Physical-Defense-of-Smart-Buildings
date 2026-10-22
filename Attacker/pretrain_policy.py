"""Warm-start the PPO attacker's move choice from a public-dataset prior (Sprint 2).

The charter: "The policy is warm-started by pretraining on public attack
datasets, then improved by reinforcement learning." Concretely, that means:
before PPO starts learning from environment reward, bias which MOVE the policy
picks towards the attack-family mix seen in CICIoT2023/TON_IoT (computed by
compute_move_prior.py), so training doesn't spend its first few thousand steps
fixated on one move before trying the others -- the mitigation the charter's
own risk table names for "lack of balance between attack types during
training". This only pretrains the MOVE choice (the first of the three
MultiDiscrete components); the two move parameters (p1, p2) have no dataset
equivalent and are left at the policy's random initialization.

This is NOT behavior cloning of real attack sequences -- the datasets are
network flows, not sequences of our moves, so there's nothing to replay move
-for-move. It is a short supervised nudge on the policy's move-selection
logits towards the dataset's attack-family distribution, using observations
sampled from the real environment so the features line up with what PPO will
see next. Normal PPO training follows immediately after, using the real
reward signal as always -- this only sets where it starts from.

Usage (standalone):
    python attacker/pretrain_policy.py --prior attacker/move_prior.json

Usage (from train_attacker.py):
    python attacker/train_attacker.py --timesteps 200000 --pretrain-prior attacker/move_prior.json

NOTE: this was written without a local stable-baselines3 install to test
against (the sandbox this was built in doesn't have it) -- it uses SB3's
public ActorCriticPolicy.get_distribution() plus the MultiCategoricalDistribution's
.distribution list, which is stable public SB3 API, but run the standalone
smoke test below first and report back anything that errors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moves import MOVES  # noqa: E402


def load_and_merge_priors(paths: list[str]) -> np.ndarray:
    """Average one or more move_prior.json files into one distribution over MOVES."""
    dists = []
    for p in paths:
        d = json.loads(Path(p).read_text())
        dists.append(np.array([d.get(m, 0.0) for m in MOVES], dtype=np.float64))
    merged = np.mean(dists, axis=0)
    merged = merged / merged.sum()
    return merged


def warm_start_from_prior(model, env, move_prior: np.ndarray, *,
                           collect_steps: int = 4000, epochs: int = 4,
                           batch_size: int = 256, verbose: bool = True) -> None:
    """Nudge `model`'s move-selection logits towards `move_prior` before RL training.

    `model` is an SB3 PPO model already built against `env` (so observation/
    action spaces match). `env` is a single (non-vectorized) AttackerEnv used
    only to generate realistic observations here -- the physical attacks this
    runs are the same ones normal training runs, just picked from the prior
    instead of the policy, so nothing about this touches real hardware.
    """
    import torch
    import torch.nn.functional as F
    from stable_baselines3.common.utils import obs_as_tensor

    policy = model.policy
    device = policy.device
    n_moves = len(MOVES)
    move_prior = np.asarray(move_prior, dtype=np.float64)
    move_prior = move_prior / move_prior.sum()

    # 1. Collect (observation, move) pairs, sampling the move from the prior.
    obs_buf, move_buf = [], []
    obs, _ = env.reset()
    for _ in range(collect_steps):
        move = int(np.random.choice(n_moves, p=move_prior))
        p1, p2 = np.random.randint(0, 5), np.random.randint(0, 5)
        obs_buf.append(obs)
        move_buf.append(move)
        obs, _, terminated, truncated, _ = env.step(np.array([move, p1, p2]))
        if terminated or truncated:
            obs, _ = env.reset()

    obs_arr = np.array(obs_buf, dtype=np.float32)
    move_arr = np.array(move_buf, dtype=np.int64)
    n = len(obs_arr)

    if verbose:
        counts = np.bincount(move_arr, minlength=n_moves)
        print("Pretraining move mix actually sampled (should roughly match the prior):")
        for i, m in enumerate(MOVES):
            print(f"  {m:14s} target {move_prior[i]*100:5.1f}%   sampled {100*counts[i]/n:5.1f}%")

    # 2. Supervised pass: push the policy's move logits towards these labels.
    for epoch in range(epochs):
        perm = np.random.permutation(n)
        total_loss = 0.0
        for start in range(0, n, batch_size):
            idx = perm[start:start + batch_size]
            obs_batch = obs_as_tensor(obs_arr[idx], device)
            move_batch = torch.as_tensor(move_arr[idx], device=device, dtype=torch.long)

            dist = policy.get_distribution(obs_batch)
            # MultiCategoricalDistribution.distribution is a list of per-sub-action
            # torch.distributions.Categorical, in the order of the MultiDiscrete's
            # nvec -- [move, p1, p2] here, so [0] is the move component.
            move_logits = dist.distribution[0].logits

            loss = F.cross_entropy(move_logits, move_batch)
            policy.optimizer.zero_grad()
            loss.backward()
            policy.optimizer.step()
            total_loss += loss.item() * len(idx)
        if verbose:
            print(f"  epoch {epoch + 1}/{epochs}  cross-entropy loss {total_loss / n:.4f}")

    if verbose:
        print("Warm start done -- the policy's move choice is now biased towards the "
              "dataset prior. Normal PPO training (model.learn(...)) picks up from here, "
              "with environment reward free to move it anywhere, including away from this.")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prior", nargs="+", required=True,
                     help="one or more move_prior.json files (averaged together)")
    ap.add_argument("--steps", type=int, default=4000)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--save", default="attacker_ppo_warmstart")
    args = ap.parse_args()

    from stable_baselines3 import PPO
    from attacker_env import AttackerEnv

    prior = load_and_merge_priors(args.prior)
    print("Merged prior:")
    for m, p in zip(MOVES, prior):
        print(f"  {m:14s} {p*100:5.1f}%")

    env = AttackerEnv()
    model = PPO("MlpPolicy", env, verbose=0, n_steps=1024, batch_size=128,
                learning_rate=3e-4, ent_coef=0.01)
    warm_start_from_prior(model, env, prior, collect_steps=args.steps, epochs=args.epochs)
    model.save(args.save)
    print(f"\nSaved warm-started (not yet RL-trained) model to {args.save}.zip")


if __name__ == "__main__":
    main()
