"""Benchmarking harness (v0, Sprint 2) for the co-evolution testbed.

Runs attacker agents against a defender on the surrogate, with fixed seeds,
and writes the numbers the project KPIs need:

  * detection rate per move (instance level and run level)
  * detection rate on SEEN vs HELD-OUT moves (the generalization KPI)
  * false-positive rate on legitimate traffic (device + operator messages)
  * physical-harm reduction: same seed and same action sequence run once with
    the defender and once with no defender
  * where the defender's autonomous actions went (attack vs legitimate traffic)
  * the trained agent's move distribution

The defender is anything with reset() and process(event) -> verdict, where the
verdict has .flagged and .commands (see pipeline.py). Optional fields read if
present: .score (0..1) and .tier. That is the same interface the Sprint 3
defender must keep, so this harness does not change when the placeholder is
replaced.

Usage (from the Attacker/ folder):
    python benchmark.py --model ../attacker_ppo.zip --episodes 20 --steps 300
    python benchmark.py --agents scripted,random --episodes 10
    python benchmark.py --defender mymodule:Defender     # Sprint 3

Outputs go to --out (default results/<timestamp>/).

Limits of v0, stated so nobody over-reads the numbers:
  * Surrogate only. Live-hardware runs are not logged to disk yet, so the
    surrogate-to-hardware consistency KPI is not computed here.
  * The defender is the PLACEHOLDER (pipeline.py) until Sprint 3. Its numbers
    show the harness works; they say nothing about the real defender.
  * physical_harm() is still the placeholder metric from pipeline.py.
  * Training seeds of attacker_ppo.zip were not recorded, so evaluation seeds
    cannot be proven disjoint from training episodes.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attacker_env import N_PARAM_LEVELS, AttackerEnv  # noqa: E402
from moves import MOVES  # noqa: E402
from pipeline import PlaceholderDefender, Verdict  # noqa: E402

HOLDOUT_DEFAULT = ("gradual_drift", "lock_replay")
LEGIT_ORIGINS = {"device", "operator"}


# --------------------------------------------------------------------- defenders
class NullDefender:
    """No defence at all. Used for the undefended run of the harm comparison."""

    def reset(self) -> None:
        pass

    def process(self, event) -> Verdict:
        return Verdict()


@dataclass
class Judgement:
    episode: int
    tick: int
    move: str
    origin: str
    topic: str
    flagged: bool
    n_commands: int
    score: float | None
    tier: str | None


class RecordingDefender:
    """Wraps any defender and records every verdict, without changing it."""

    def __init__(self, inner):
        self.inner = inner
        self.records: list[Judgement] = []
        self.episode = 0
        self.tick = 0
        self.move = "noop"

    def reset(self) -> None:
        self.inner.reset()

    def process(self, event):
        v = self.inner.process(event)
        self.records.append(Judgement(
            self.episode, self.tick, self.move, event.origin, event.topic,
            bool(v.flagged), len(v.commands),
            getattr(v, "score", None), getattr(v, "tier", None)))
        return v


def load_defender(spec: str):
    if spec == "placeholder":
        return PlaceholderDefender()
    if spec == "null":
        return NullDefender()
    module, _, cls = spec.partition(":")
    return getattr(importlib.import_module(module), cls)()


# ---------------------------------------------------------------------- agents
class Agent:
    name = "base"

    def reset(self, rng: np.random.Generator) -> None:
        self.rng = rng

    def act(self, obs: np.ndarray) -> tuple[int, int, int]:
        raise NotImplementedError


class NoAttack(Agent):
    name = "no_attack"

    def act(self, obs):
        return 0, 0, 0


class RandomAgent(Agent):
    def __init__(self, exclude: tuple[str, ...] = ()):
        self.allowed = [i for i, m in enumerate(MOVES) if m not in exclude]
        self.name = "random" + ("_seen_only" if exclude else "")

    def act(self, obs):
        return (int(self.rng.choice(self.allowed)),
                int(self.rng.integers(N_PARAM_LEVELS)),
                int(self.rng.integers(N_PARAM_LEVELS)))


class ScriptedAgent(Agent):
    """Plays one move every tick with random parameters. Isolates per-move detection."""

    def __init__(self, move: str):
        self.move = MOVES.index(move)
        self.name = f"scripted:{move}"

    def act(self, obs):
        return (self.move, int(self.rng.integers(N_PARAM_LEVELS)),
                int(self.rng.integers(N_PARAM_LEVELS)))


class PPOAgent(Agent):
    def __init__(self, path: str, stochastic: bool):
        from stable_baselines3 import PPO
        self.model = PPO.load(path, device="cpu")
        self.stochastic = stochastic
        self.name = "ppo_stochastic" if stochastic else "ppo_greedy"

    def act(self, obs):
        a, _ = self.model.predict(obs, deterministic=not self.stochastic)
        return tuple(int(x) for x in a)


class ReplayAgent(Agent):
    """Replays a fixed action list. Used to run the identical sequence undefended."""

    def __init__(self, actions, name):
        self.actions = actions
        self.name = name

    def reset(self, rng):
        super().reset(rng)
        self.i = 0

    def act(self, obs):
        a = self.actions[self.i] if self.i < len(self.actions) else (0, 0, 0)
        self.i += 1
        return a


# ------------------------------------------------------------------- the runner
@dataclass
class EpisodeResult:
    agent: str
    episode: int
    seed: int
    steps: int
    harm_defended: float        # sum of per-tick caused-harm, this (defended) run
    valid_attacks: int
    moves: list                 # (move_idx, valid) per tick, to replay undefended
    actions: list               # (move,i1,i2) per tick, to replay undefended


def run_episode(agent: Agent, env: AttackerEnv, seed: int,
                rec: RecordingDefender | None, ep_index: int) -> EpisodeResult:
    """One episode against the given (already-wired) defender env."""
    obs, _ = env.reset(seed=seed)
    agent.reset(np.random.default_rng(seed))
    harm = 0.0
    valid_attacks = 0
    moves, actions = [], []
    done = False
    tick = 0
    while not done:
        action = agent.act(obs)
        if rec is not None:
            rec.episode, rec.tick, rec.move = ep_index, tick, MOVES[int(action[0])]
        obs, reward, terminated, truncated, info = env.step(action)
        harm += info["harm"]
        is_attack = info["move"] != "noop" and info["valid"]
        valid_attacks += int(is_attack)
        moves.append((int(action[0]), bool(info["valid"])))
        actions.append(tuple(int(x) for x in action))
        done = terminated or truncated
        tick += 1
    return EpisodeResult(agent.name, ep_index, seed, tick, harm,
                         valid_attacks, moves, actions)


def make_env(defender, steps: int) -> AttackerEnv:
    """An env whose defender is the one we pass in (not a fresh placeholder)."""
    env = AttackerEnv(max_steps=steps)
    env.defender = defender
    return env


# ---------------------------------------------------------------------- metrics
def summarise(records: list[Judgement], holdout: tuple[str, ...]) -> dict:
    """Detection and false-positive numbers from the recorded verdicts."""
    seen_moves = [m for m in MOVES if m not in holdout and m != "noop"]

    # Attack instances: a message the defender judged that came from the attacker.
    atk = [r for r in records if r.origin == "attacker"]
    legit = [r for r in records if r.origin in LEGIT_ORIGINS]

    def rate(rows):
        return (sum(r.flagged for r in rows) / len(rows)) if rows else None

    per_move = {}
    for m in MOVES:
        if m == "noop":
            continue
        rows = [r for r in atk if r.move == m]
        per_move[m] = {"instances": len(rows), "detection_rate": rate(rows),
                       "held_out": m in holdout}

    seen_rows = [r for r in atk if r.move in seen_moves]
    held_rows = [r for r in atk if r.move in holdout]

    return {
        "attack_instances_judged": len(atk),
        "overall_detection_rate": rate(atk),
        "detection_rate_seen_moves": rate(seen_rows),
        "detection_rate_held_out_moves": rate(held_rows),
        "generalization_gap": (
            (rate(seen_rows) - rate(held_rows))
            if seen_rows and held_rows else None),
        "legit_messages_judged": len(legit),
        "false_positive_rate": rate(legit),
        "false_positives_by_origin": {
            o: rate([r for r in legit if r.origin == o])
            for o in sorted({r.origin for r in legit})},
        "defender_actions_total": sum(r.n_commands for r in records),
        "defender_actions_vs_attack": sum(r.n_commands for r in atk),
        "defender_actions_vs_legit": sum(r.n_commands for r in legit),
        "per_move": per_move,
    }


def harm_reduction(results: list[EpisodeResult], steps: int) -> dict:
    """Replay each episode's exact action sequence with NO defender, and compare
    caused-harm to the defended run. Positive reduction = the defender helped."""
    defended = sum(r.harm_defended for r in results)
    undef = 0.0
    for r in results:
        env = make_env(NullDefender(), steps)
        replay = ReplayAgent(r.actions, "replay")
        u = run_episode(replay, env, r.seed, None, r.episode)
        undef += u.harm_defended
    reduction = (undef - defended)
    pct = (reduction / undef * 100.0) if undef else None
    return {"caused_harm_defended": defended,
            "caused_harm_undefended": undef,
            "harm_reduction": reduction,
            "harm_reduction_pct": pct}


# ------------------------------------------------------------------------- main
def build_agents(args) -> list[Agent]:
    chosen = [a.strip() for a in args.agents.split(",") if a.strip()]
    agents: list[Agent] = []
    for a in chosen:
        if a == "ppo":
            if not args.model:
                print("skip ppo: no --model given"); continue
            agents.append(PPOAgent(args.model, stochastic=not args.greedy))
        elif a == "random":
            agents.append(RandomAgent())
        elif a == "random_seen":
            agents.append(RandomAgent(exclude=tuple(args.holdout)))
        elif a == "no_attack":
            agents.append(NoAttack())
        elif a == "scripted":
            for m in MOVES:
                if m != "noop":
                    agents.append(ScriptedAgent(m))
        elif a.startswith("scripted:"):
            agents.append(ScriptedAgent(a.split(":", 1)[1]))
        else:
            print(f"skip unknown agent {a!r}")
    return agents


def write_csvs(out: Path, records, results):
    with (out / "messages.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent", "episode", "tick", "move", "origin", "topic",
                    "flagged", "n_commands", "score", "tier"])
        for agent, recs in records.items():
            for r in recs:
                w.writerow([agent, r.episode, r.tick, r.move, r.origin, r.topic,
                            int(r.flagged), r.n_commands, r.score, r.tier])
    with (out / "episodes.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent", "episode", "seed", "steps", "harm_defended", "valid_attacks"])
        for r in results:
            w.writerow([r.agent, r.episode, r.seed, r.steps,
                        round(r.harm_defended, 4), r.valid_attacks])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", default="ppo,random,scripted",
                    help="comma list: ppo, random, random_seen, no_attack, "
                         "scripted (all moves), scripted:<move>, no_attack")
    ap.add_argument("--model", default=None, help="path to the PPO .zip for the ppo agent")
    ap.add_argument("--greedy", action="store_true", help="ppo: argmax instead of sampling")
    ap.add_argument("--defender", default="placeholder",
                    help="placeholder | null | module:Class (Sprint 3 defender)")
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--seed-base", type=int, default=10_000,
                    help="eval seeds are seed-base + episode index (kept away from training)")
    ap.add_argument("--holdout", default=",".join(HOLDOUT_DEFAULT),
                    help="comma list of moves treated as held-out for the generalization KPI")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args.holdout = tuple(m.strip() for m in args.holdout.split(",") if m.strip())

    out = Path(args.out) if args.out else Path("results") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    agents = build_agents(args)
    if not agents:
        print("no agents to run"); return

    all_records: dict[str, list[Judgement]] = {}
    all_results: list[EpisodeResult] = []
    summary: dict = {"config": {"defender": args.defender, "episodes": args.episodes,
                                "steps": args.steps, "seed_base": args.seed_base,
                                "holdout": list(args.holdout), "model": args.model},
                     "agents": {}}

    for agent in agents:
        rec = RecordingDefender(load_defender(args.defender))
        env = make_env(rec, args.steps)
        results = []
        move_counter: Counter = Counter()
        for ep in range(args.episodes):
            seed = args.seed_base + ep
            r = run_episode(agent, env, seed, rec, ep)
            results.append(r)
            for mv, valid in r.moves:
                move_counter[MOVES[mv]] += 1
        all_records[agent.name] = rec.records
        all_results += results

        det = summarise(rec.records, args.holdout)
        harm = harm_reduction(results, args.steps)
        summary["agents"][agent.name] = {
            **det, **harm,
            "move_distribution": dict(move_counter),
            "mean_valid_attacks_per_ep": round(
                sum(r.valid_attacks for r in results) / len(results), 2),
        }
        print(f"[{agent.name}] det={det['overall_detection_rate']} "
              f"FPR={det['false_positive_rate']} "
              f"harm_red%={harm['harm_reduction_pct']}")

    write_csvs(out, all_records, all_results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "summary.md").write_text(render_md(summary))
    print(f"\nWrote results to {out}/")


def render_md(summary: dict) -> str:
    c = summary["config"]
    lines = ["# Benchmark results", "",
             f"- defender: `{c['defender']}`  (placeholder until Sprint 3)",
             f"- episodes/agent: {c['episodes']}  x  {c['steps']} ticks",
             f"- eval seeds: {c['seed_base']}..{c['seed_base']+c['episodes']-1}",
             f"- held-out moves: {', '.join(c['holdout'])}", "",
             "| agent | det. all | det. seen | det. held-out | gap | FPR | harm red. % |",
             "|---|---|---|---|---|---|---|"]

    def f(x):
        return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))

    for name, a in summary["agents"].items():
        lines.append(f"| {name} | {f(a['overall_detection_rate'])} | "
                     f"{f(a['detection_rate_seen_moves'])} | "
                     f"{f(a['detection_rate_held_out_moves'])} | "
                     f"{f(a['generalization_gap'])} | {f(a['false_positive_rate'])} | "
                     f"{f(a['harm_reduction_pct'])} |")
    lines += ["", "## Per-move detection (first agent with attacks)"]
    for name, a in summary["agents"].items():
        if a["attack_instances_judged"]:
            lines += [f"", f"### {name}", "",
                      "| move | held-out | instances | detection rate |",
                      "|---|---|---|---|"]
            for m, d in a["per_move"].items():
                lines.append(f"| {m} | {'yes' if d['held_out'] else ''} | "
                             f"{d['instances']} | {f(d['detection_rate'])} |")
            break
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
