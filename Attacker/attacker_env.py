"""Gymnasium training environment for the attacker.

One env step = one 2 s firmware tick:
  1. the agent picks a move (+ two parameters) and it is executed (moves.py)
  2. the surrogate advances one tick (sensors publish, people act)
  3. the defender stand-in processes every message and may send commands back
  4. reward = harm the attacker CAUSED  -  penalty for attacker messages the defender
              flagged  -  small cost for acting  -  penalty for an invalid move

"Harm the attacker caused" = harm in the attacked world minus harm in a shadow world
that runs the same seed with no attacker. Harm that would have happened anyway earns
nothing, and an attack that is undone by the defender earns nothing.

Observation (what a remote attacker can see on the broker, never hidden truth):
  last K=8 ticks x [temp, humidity, motion, lock_open, fan_on, got_temp]
  + one-hot of the previous move + its two parameters + "a captured unlock exists"

Action: MultiDiscrete([7, 5, 5]) = move (0 noop, 1..6), param1, param2 in {0,.25,.5,.75,1}.

The reward weights and the harm metric are PLACEHOLDERS (check pipeline.py).
"""

from __future__ import annotations

from collections import deque

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from moves import MOVES, AttackerState, AttackerView, captured_unlock_commands, execute
from pipeline import PlaceholderDefender, physical_harm
from surrogate import BuildingSurrogate, Event, SurrogateConfig

K = 8
N_FEATURES = 6
N_PARAM_LEVELS = 5


class AttackerEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, surrogate_config: SurrogateConfig | None = None, defender=None,
                 max_steps: int = 300, w_harm: float = 1.0, w_detected: float = 0.5,
                 w_cost: float = 0.01, w_invalid: float = 0.05):
        super().__init__()
        cfg = surrogate_config or SurrogateConfig(skip_pir_warmup=True)
        self.sim = BuildingSurrogate(cfg, seed=0)
        self.defender = defender or PlaceholderDefender()
        self.shadow_defender = PlaceholderDefender()
        self.shadow = BuildingSurrogate(cfg, seed=0)
        self.max_steps = max_steps
        self.w = dict(harm=w_harm, detected=w_detected, cost=w_cost, invalid=w_invalid)

        self.action_space = spaces.MultiDiscrete([len(MOVES), N_PARAM_LEVELS, N_PARAM_LEVELS])
        obs_dim = K * N_FEATURES + len(MOVES) + 2 + 1
        self.observation_space = spaces.Box(-5.0, 5.0, shape=(obs_dim,), dtype=np.float32)

    # ------------------------------------------------------------------ API
    def reset(self, *, seed: int | None = None, options=None):
        super().reset(seed=seed)
        seed = seed if seed is not None else int(self.np_random.integers(2**31))
        boot = self.sim.reset(seed=seed)
        self.shadow.reset(seed=seed)
        self.defender.reset()
        self.shadow_defender.reset()
        self.view = AttackerView()
        self.state = AttackerState()
        self.steps = 0
        self.last_action = (0, 0.0, 0.0)
        self._cur = {"temp": 0.0, "hum": 0.0, "motion": 0.0, "lock_open": 0.0,
                     "fan_on": 0.0}
        self._window = deque([np.zeros(N_FEATURES, np.float32) for _ in range(K)], maxlen=K)
        self._ingest(boot)
        self._push_window(got_temp=0.0)
        return self._obs(), {}

    def step(self, action):
        move, i1, i2 = (int(a) for a in action)
        p1, p2 = i1 / (N_PARAM_LEVELS - 1), i2 / (N_PARAM_LEVELS - 1)

        mark = len(self.sim.log)
        attacker_events, valid = execute(self.sim, self.view, self.state, move, p1, p2)
        self.sim.step()
        events = list(self.sim.log[mark:])      # everything on the wire this tick, in order

        detected = self._run_defender(self.sim, self.defender, events, count_for="attacker")
        self._ingest(events)

        shadow_mark = len(self.shadow.log)
        self.shadow.step()
        self._run_defender(self.shadow, self.shadow_defender, list(self.shadow.log[shadow_mark:]))

        truth = self.sim.truth()
        harm_raw = physical_harm(truth, self.sim.cfg.ambient_c)
        harm = harm_raw - physical_harm(self.shadow.truth(), self.sim.cfg.ambient_c)
        reward = (self.w["harm"] * harm
                  - self.w["detected"] * detected
                  - (self.w["cost"] if move != 0 else 0.0)
                  - (self.w["invalid"] if not valid else 0.0))

        self.last_action = (move, p1, p2)
        self.steps += 1
        got_temp = float(any(e.is_telemetry and e.component_id == "temp_1" for e in events))
        self._push_window(got_temp)
        truncated = self.steps >= self.max_steps
        info = {"move": MOVES[move], "valid": valid, "harm": harm, "harm_raw": harm_raw,
                "detected": detected,
                "truth": truth, "attacker_events": len(attacker_events)}
        return self._obs(), float(reward), False, truncated, info

    # ------------------------------------------------------------ internals
    def _run_defender(self, sim: BuildingSurrogate, defender, events: list[Event],
                      count_for: str | None = None) -> int:
        """Feed every message (telemetry and commands) to the defender and deliver
        what it sends back. Returns how many messages from `count_for` it flagged."""
        detected = 0
        queue = list(events)
        guard = 0
        while queue and guard < 100:
            guard += 1
            ev = queue.pop(0)
            verdict = defender.process(ev)
            if verdict.flagged and count_for and ev.origin == count_for:
                detected += 1
            for target, payload in verdict.commands:
                before = len(sim.log)
                sim.deliver_command(target, payload, origin="backend")
                responses = [e for e in sim.log[before:] if e.is_telemetry]
                if sim is self.sim:
                    self._ingest(responses)
                queue += responses
        return detected

    def _ingest(self, events: list[Event]) -> None:
        self.view.update(events)
        c = self._cur
        for e in events:
            if not e.is_telemetry:
                continue
            d, cid = e.payload["data"], e.component_id
            if cid == "temp_1":
                c["temp"] = (d["temperature"] - 24.0) / 10.0
                c["hum"] = (d["humidity"] - 48.0) / 20.0
            elif cid == "motion_1":
                c["motion"] = float(d["motion"])
            elif cid == "lock_1":
                c["lock_open"] = float(d["state"] == "unlocked")
            elif cid == "fan_1":
                c["fan_on"] = float(d["state"] == "on")

    def _push_window(self, got_temp: float) -> None:
        c = self._cur
        self._window.append(np.array(
            [c["temp"], c["hum"], c["motion"], c["lock_open"], c["fan_on"], got_temp],
            dtype=np.float32))

    def _obs(self) -> np.ndarray:
        move, p1, p2 = self.last_action
        onehot = np.zeros(len(MOVES), np.float32)
        onehot[move] = 1.0
        has_capture = float(len(captured_unlock_commands(self.sim)) > 0)
        obs = np.concatenate([np.concatenate(self._window), onehot,
                              np.array([p1, p2, has_capture], np.float32)])
        return np.clip(obs, -5.0, 5.0).astype(np.float32)
