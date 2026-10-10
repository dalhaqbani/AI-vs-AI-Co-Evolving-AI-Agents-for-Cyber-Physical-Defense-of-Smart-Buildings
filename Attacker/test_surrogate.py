"""Checks that the surrogate matches the real pipeline's interface.

Run from the repo root:  python -m pytest attacker/test_surrogate.py -q
Needs the backend requirements installed (fastapi, paho-mqtt, pydantic).
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "attacker"))
os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.mkdtemp()) / "readings.db"))

import main as backend  # noqa: E402  (the real backend: schema + command rules)
from ai_engine import evaluate  # noqa: E402
from surrogate import BuildingSurrogate, SurrogateConfig  # noqa: E402


def run(seed, ticks=500, **cfg):
    env = BuildingSurrogate(SurrogateConfig(**cfg), seed=seed)
    events = list(env.log)
    for _ in range(ticks):
        events += env.step()
    return env, events


def telemetry(events):
    return [e for e in events if e.is_telemetry]


# ---- interface contract with the real backend -------------------------------

def test_every_message_passes_the_backend_schema_and_ai_engine():
    for seed in range(5):
        _, events = run(seed, ticks=1000)
        assert len(telemetry(events)) > 500
        for e in telemetry(events):
            reading = backend.telemetry_adapter.validate_json(e.payload_json())
            assert reading.component_id == e.component_id
            decision = evaluate(reading.component_type, reading.data.model_dump())
            assert decision.status in {"normal", "warning", "critical"}


def test_all_five_components_are_present_with_the_right_topics():
    _, events = run(0, ticks=3000)
    seen = {(e.topic, e.payload["component_type"]) for e in telemetry(events)}
    assert seen == {
        ("building/temp_1/telemetry", "dht22"),
        ("building/motion_1/telemetry", "pir"),
        ("building/lock_1/telemetry", "servo_lock"),
        ("building/fan_1/telemetry", "fan_led"),
        ("building/button_1/telemetry", "push_button"),
    }


def test_payload_key_order_matches_firmware():
    _, events = run(0, ticks=5)
    for e in telemetry(events):
        assert list(e.payload) == ["component_id", "component_type", "timestamp", "is_test", "data"]
        assert e.payload["is_test"] is False


# ---- firmware behaviour ------------------------------------------------------

def test_boot_publishes_lock_locked_and_fan_off():
    env = BuildingSurrogate(seed=1)
    boot = [(e.component_id, e.payload["data"]["state"]) for e in env.log]
    assert boot == [("lock_1", "locked"), ("fan_1", "off")]


def test_pir_is_silent_for_the_first_60_seconds():
    _, events = run(2, ticks=29, p_arrive=1.0)       # 29 ticks = 58 s
    assert not [e for e in events if e.component_id == "motion_1"]


def test_pir_and_button_publish_only_on_change():
    _, events = run(3, ticks=4000, skip_pir_warmup=True)
    for cid, key in (("motion_1", "motion"), ("button_1", "state")):
        values = [e.payload["data"][key] for e in events if e.component_id == cid and e.is_telemetry]
        assert len(values) > 10
        assert all(a != b for a, b in zip(values, values[1:])), cid


def test_dht22_publishes_about_every_tick_and_sometimes_fails():
    _, events = run(4, ticks=2000)
    n = len([e for e in events if e.component_id == "temp_1"])
    assert 1950 < n < 2000


def test_lock_commands_follow_firmware_substring_rules():
    env = BuildingSurrogate(seed=5)
    out = env.deliver_command("lock_1", {"action": "unlock", "source": "dashboard"})
    assert env.locked is False and out[0].payload["data"]["state"] == "unlocked"
    out = env.deliver_command("lock_1", {"action": "lock", "source": "dashboard"})
    assert env.locked is True and out[0].payload["data"]["state"] == "locked"
    # firmware publishes the lock state after any matching command, even a repeat
    out = env.deliver_command("lock_1", {"action": "lock"})
    assert len(out) == 1
    # an action the lock does not understand is ignored and publishes nothing
    assert env.deliver_command("lock_1", {"action": "reset"}) == []
    assert env.locked is True


def test_safe_mode_toggles_the_fan():
    env = BuildingSurrogate(seed=6)
    env.deliver_command("fan_1", {"action": "safe_mode", "source": "dashboard"})
    assert env.fan_on is True
    env.deliver_command("fan_1", {"action": "safe_mode", "source": "dashboard"})
    assert env.fan_on is False


def test_commands_for_other_components_do_nothing():
    # The real backend answers a hot temp_1 reading with safe_mode on
    # building/temp_1/commands. The firmware only listens on lock_1 and fan_1.
    env = BuildingSurrogate(seed=7)
    assert env.deliver_command("temp_1", {"action": "safe_mode"}) == []
    assert env.fan_on is False


def test_every_command_the_backend_can_send_is_accepted_by_the_surrogate():
    for action in ("safe_mode", "normal_mode", "inspect", "lock", "unlock", "reset"):
        backend.Command(component_id="lock_1", action=action)
        BuildingSurrogate(seed=0).deliver_command("lock_1", {"action": action})


# ---- physics and attack surface ---------------------------------------------

def test_temperature_stays_in_a_plausible_range():
    for seed in range(5):
        _, events = run(seed, ticks=5000)
        temps = [e.payload["data"]["temperature"] for e in events if e.component_id == "temp_1"]
        assert 18 < min(temps) and max(temps) < 32
        hums = [e.payload["data"]["humidity"] for e in events if e.component_id == "temp_1"]
        assert 30 < min(hums) and max(hums) < 70


def test_fan_cools_the_room():
    def mean_temp(fan):
        vals = []
        for seed in range(10):
            env = BuildingSurrogate(seed=seed)
            env.fan_on = fan
            for _ in range(300):
                env.step()
                vals.append(env.true_temp)
        return np.mean(vals)
    assert mean_temp(True) < mean_temp(False) - 0.5


def test_forged_telemetry_changes_the_wire_not_the_room():
    env = BuildingSurrogate(seed=8)
    before = env.truth()
    ev = env.inject_telemetry("temp_1", {"temperature": 60.0, "humidity": 48.0})
    assert ev.origin == "attacker"
    assert backend.telemetry_adapter.validate_json(ev.payload_json()).data.temperature == 60.0
    assert env.truth() == before


def test_injected_unlock_really_unlocks_and_is_labelled():
    env = BuildingSurrogate(seed=9)
    out = env.deliver_command("lock_1", {"action": "unlock", "source": "dashboard"}, origin="attacker")
    assert env.truth()["locked"] is False
    assert out[0].payload["data"]["state"] == "unlocked"
    cmd = [e for e in env.log if e.topic.endswith("/commands")][-1]
    assert cmd.origin == "attacker"


def test_operator_traffic_exists_and_is_labelled_benign():
    env, events = run(10, ticks=5000, p_operator_command=0.05)
    ops = [e for e in env.log if e.topic.endswith("/commands")]
    assert ops and all(e.origin == "operator" for e in ops)


def test_recent_returns_capturable_messages_for_replay():
    env, _ = run(11, ticks=50)
    captured = env.recent("building/temp_1/telemetry", n=5)
    assert len(captured) == 5 and all(e.origin == "device" for e in captured)


# ---- reproducibility ----------------------------------------------------------

def test_same_seed_same_episode_different_seed_different_episode():
    _, a = run(42, ticks=300)
    _, b = run(42, ticks=300)
    _, c = run(43, ticks=300)
    assert [e.payload for e in a] == [e.payload for e in b]
    assert [e.payload for e in a] != [e.payload for e in c]


def test_reset_with_seed_restarts_the_episode():
    env = BuildingSurrogate(seed=1)
    first = [e.payload for _ in range(50) for e in env.step()]
    env.reset(seed=1)
    second = [e.payload for _ in range(50) for e in env.step()]
    assert first == second
