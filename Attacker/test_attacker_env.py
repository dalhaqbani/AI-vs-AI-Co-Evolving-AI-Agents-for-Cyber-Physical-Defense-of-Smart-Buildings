"""Tests for the six moves and the training environment.

Run from the repo root:  python -m pytest attacker -q
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attacker_env import AttackerEnv  # noqa: E402
from moves import MOVES, AttackerState, AttackerView, execute  # noqa: E402
from pipeline import PlaceholderDefender, physical_harm  # noqa: E402
from surrogate import BuildingSurrogate, SurrogateConfig  # noqa: E402

NOOP, SPOOF, BIAS, DRIFT, FREEZE, REPLAY, INJECT, PIRSPOOF = range(8)
CALM = dict(p_arrive=0.0, p_leave=1.0, skip_pir_warmup=True, p_dht_fail=0.0,
            p_operator_command=0.0)          # empty room, no random benign traffic


def a(move, p1=0, p2=0):
    return np.array([move, p1, p2])          # params are indices 0..4


def calm_env(seed=0, **kw):
    env = AttackerEnv(SurrogateConfig(**CALM), **kw)
    env.reset(seed=seed)
    return env


# ---- environment contract ----------------------------------------------------

def test_gymnasium_env_checker_passes():
    check_env(AttackerEnv(), skip_render_check=True)


def test_random_policy_runs_full_episodes_with_finite_values():
    env = AttackerEnv(max_steps=120)
    for seed in range(5):
        obs, _ = env.reset(seed=seed)
        done = False
        steps = 0
        while not done:
            obs, r, terminated, truncated, info = env.step(env.action_space.sample())
            assert np.isfinite(obs).all() and np.isfinite(r)
            assert env.observation_space.contains(obs)
            done = terminated or truncated
            steps += 1
        assert steps == 120


def test_same_seed_same_trajectory():
    def rollout():
        env = AttackerEnv(max_steps=60)
        env.reset(seed=3)
        env.action_space.seed(3)
        return [round(env.step(env.action_space.sample())[1], 6) for _ in range(60)]
    assert rollout() == rollout()


# ---- individual moves ---------------------------------------------------------

def test_noop_does_nothing_and_costs_nothing():
    env = calm_env()
    _, r, _, _, info = env.step(a(NOOP))
    assert info["attacker_events"] == 0 and info["valid"]
    assert r == pytest.approx(0.0, abs=1e-9)      # harm that happens anyway earns nothing


def test_temp_spoof_forges_the_chosen_value_and_is_labelled():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    ev, ok = execute(sim, AttackerView(), AttackerState(), SPOOF, 1.0, 0.0)
    assert ok and ev[0].origin == "attacker"
    assert ev[0].payload["data"]["temperature"] == 50.0
    ev, _ = execute(sim, AttackerView(), AttackerState(), SPOOF, 0.0, 0.0)
    assert ev[0].payload["data"]["temperature"] == 10.0


def test_const_bias_is_real_temperature_plus_offset():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    view = AttackerView()
    view.update(sim.step())
    real = view.real_temp
    ev, ok = execute(sim, view, AttackerState(), BIAS, 1.0, 0.0)      # +10 C
    assert ok and ev[0].payload["data"]["temperature"] == pytest.approx(real + 10.0, abs=0.1)


def test_bias_and_drift_need_a_real_reading_first():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    for move in (BIAS, DRIFT):
        _, ok = execute(sim, AttackerView(), AttackerState(), move, 0.5, 1.0)
        assert not ok


def test_gradual_drift_accumulates_and_resets_after_another_move():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    view, state = AttackerView(), AttackerState()
    view.update(sim.step())
    offsets = []
    for _ in range(5):
        execute(sim, view, state, DRIFT, 1.0, 1.0)          # +0.5 C per tick
        offsets.append(state.drift_offset)
    assert offsets == pytest.approx([0.5, 1.0, 1.5, 2.0, 2.5])
    execute(sim, view, state, NOOP, 0, 0)
    execute(sim, view, state, DRIFT, 1.0, 1.0)
    assert state.drift_offset == pytest.approx(0.5)


def test_pir_freeze_publishes_one_stuck_value():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    for p, want in ((1.0, True), (0.0, False)):
        ev, ok = execute(sim, AttackerView(), AttackerState(), FREEZE, p, 0.0)
        assert ok and ev[0].payload["data"] == {"motion": want} and ev[0].origin == "attacker"


def test_lock_replay_needs_a_capture_and_then_really_unlocks():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    _, ok = execute(sim, AttackerView(), AttackerState(), REPLAY, 0.0, 0.0)
    assert not ok                                              # nothing captured yet
    sim.deliver_command("lock_1", {"action": "unlock", "source": "dashboard"}, origin="operator")
    sim.deliver_command("lock_1", {"action": "lock", "source": "dashboard"}, origin="operator")
    assert sim.locked
    ev, ok = execute(sim, AttackerView(), AttackerState(), REPLAY, 0.0, 0.0)
    assert ok and sim.locked is False
    assert ev[0].payload["data"]["state"] == "unlocked"
    assert [e for e in sim.log if e.topic.endswith("/commands")][-1].origin == "attacker"


def test_command_injection_targets_lock_or_fan():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    execute(sim, AttackerView(), AttackerState(), INJECT, 0.0, 0.0)    # lock_1 unlock
    assert sim.locked is False
    execute(sim, AttackerView(), AttackerState(), INJECT, 0.0, 1.0)    # lock_1 lock
    assert sim.locked is True
    execute(sim, AttackerView(), AttackerState(), INJECT, 1.0, 0.0)    # fan_1 safe_mode
    assert sim.fan_on is True


# ---- reward and defender stand-in --------------------------------------------

def test_unlocking_an_empty_room_is_caught_and_relocked():
    env = calm_env()
    _, r, _, _, info = env.step(a(INJECT, 0, 0))
    assert info["detected"] == 1                      # unlock with no one present
    assert env.sim.locked is True                     # the defender locked it again
    assert info["harm"] == 0.0 and r < 0


def test_a_noop_episode_earns_exactly_zero_for_every_seed():
    for seed in range(5):
        env = AttackerEnv(max_steps=200)
        env.reset(seed=seed)
        total = sum(env.step(a(NOOP))[1] for _ in range(200))
        assert total == pytest.approx(0.0, abs=1e-9)      # shadow world stays in lock-step


def test_harm_is_measured_against_the_no_attacker_world():
    env = calm_env()
    _, _, _, _, info = env.step(a(SPOOF, 4, 0))       # claim 50 C -> defender runs the fan
    assert env.sim.fan_on and not env.shadow.fan_on
    assert info["harm"] == pytest.approx(0.3)         # fan running in a cool room, caused by the attack
    assert info["harm_raw"] >= info["harm"]


def test_defender_flags_unlock_only_when_nobody_is_present():
    d = PlaceholderDefender()
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    sim.t = 100.0
    sim.deliver_command("lock_1", {"action": "unlock"}, origin="attacker")
    cmd = [e for e in sim.log if e.topic.endswith("/commands")][-1]
    assert d.process(cmd).flagged                     # no motion ever seen
    d.process(sim.inject_telemetry("motion_1", {"motion": True}))
    d.process(sim.inject_telemetry("motion_1", {"motion": False}))
    sim.t = 110.0                                     # 10 s after the last motion
    sim.deliver_command("lock_1", {"action": "unlock"}, origin="attacker")
    cmd = [e for e in sim.log if e.topic.endswith("/commands")][-1]
    assert not d.process(cmd).flagged                 # someone was just here
    sim.t = 200.0                                     # long after
    sim.deliver_command("lock_1", {"action": "unlock"}, origin="attacker")
    cmd = [e for e in sim.log if e.topic.endswith("/commands")][-1]
    verdict = d.process(cmd)
    assert verdict.flagged and verdict.commands[0][0] == "lock_1"


def test_unlocking_an_occupied_room_is_not_harm():
    assert physical_harm({"locked": False, "occupied": True, "fan_on": False, "temp_c": 24.0}) == 0.0
    assert physical_harm({"locked": False, "occupied": False, "fan_on": False, "temp_c": 24.0}) == 1.0


def test_a_hot_spoof_is_flagged_and_makes_the_fan_run_until_the_heat_is_gone():
    env = calm_env()
    _, _, _, _, info = env.step(a(SPOOF, 4, 0))                # claim 50 C
    assert info["detected"] == 1
    assert env.sim.fan_on is True                              # the defender reacted
    for _ in range(3):                                         # attacker stops; readings normal
        env.step(a(NOOP))
    assert env.sim.fan_on is False                             # defender switched it back off


def test_a_steady_spoof_alternating_with_real_readings_is_flagged_by_the_jump_rule():
    env = calm_env()
    env.step(a(NOOP))                                          # let the attacker see a real reading
    flagged = sum(env.step(a(SPOOF, 2, 0))[4]["detected"] for _ in range(6))   # claim 30.0 C
    assert flagged >= 5                                        # 30 vs ~24 keeps jumping


def test_a_slow_drift_stays_under_the_placeholder_defenders_radar():
    env = calm_env()
    env.step(a(NOOP))
    for _ in range(6):
        _, _, _, _, info = env.step(a(DRIFT, 0, 4))            # +0.1 C per tick
        assert info["detected"] == 0


def test_defender_does_not_toggle_a_fan_that_is_already_on():
    d = PlaceholderDefender()
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    hot = sim.inject_telemetry("temp_1", {"temperature": 40.0, "humidity": 48.0})
    assert len(d.process(hot).commands) == 1
    d.process(sim.inject_telemetry("fan_1", {"state": "on"}))
    assert d.process(hot).commands == []


def test_attacker_cannot_read_hidden_truth():
    env = calm_env()
    obs, *_ = env.step(a(INJECT, 0, 0))
    assert obs.shape == env.observation_space.shape
    # lock_open feature reflects the lock telemetry it saw, not a direct read of truth
    assert obs[-(len(MOVES) + 3) - 6 + 3] in (0.0, 1.0)


def test_move_names_line_up_with_the_attacker_design():
    assert MOVES[1:] == ["temp_spoof", "const_bias", "gradual_drift",
                         "pir_freeze", "lock_replay", "cmd_inject",
                         "pir_spoof"]


def test_pir_spoof_is_a_single_change_and_rejects_repeats():
    sim = BuildingSurrogate(SurrogateConfig(**CALM), seed=0)
    view, state = AttackerView(), AttackerState()
    ev, ok = execute(sim, view, state, PIRSPOOF, 1.0, 0.0)
    assert ok and ev[0].payload["data"] == {"motion": True} and ev[0].origin == "attacker"
    view.update(ev)
    _, ok = execute(sim, view, state, PIRSPOOF, 1.0, 0.0)       # same value again
    assert not ok
    ev, ok = execute(sim, view, state, PIRSPOOF, 0.0, 0.0)
    assert ok and ev[0].payload["data"] == {"motion": False}


def test_repeated_fake_motion_is_flagged_and_gives_no_cover_for_an_unlock():
    env = calm_env()
    env.step(a(FREEZE, 4, 0))                                   # first True is a change
    detected = sum(env.step(a(FREEZE, 4, 0))[4]["detected"] for _ in range(14))
    assert detected == 14                                       # every repeat is flagged
    # only the first forged change counted as presence; 20 s later it has expired
    env.step(a(INJECT, 0, 0))
    assert env.sim.locked is True                               # unlock still caught


def test_a_single_forged_motion_change_still_fools_the_placeholder():
    env = calm_env()
    _, _, _, _, info = env.step(a(PIRSPOOF, 4, 0))
    assert info["detected"] == 0
    env.step(a(INJECT, 0, 0))
    assert env.sim.locked is False                              # the next hole co-evolution must close
