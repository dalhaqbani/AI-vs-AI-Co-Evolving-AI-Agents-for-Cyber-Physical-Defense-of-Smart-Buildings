"""Run each attacker move ONCE against the real broker and print what happened.

This is a manual, supervised check — not the training loop, and not something
to schedule or leave running unattended. It is meant for you, watching the
dashboard and the physical hardware, confirming each move does what moves.py
says it should. Run it from the repo root:

    set ALLOW_LIVE_ATTACKS=1          (Windows: set, macOS/Linux: export)
    python attacker/run_live_smoke_test.py

Covers all 7 attacker moves, including gradual_drift and lock_replay -- those
are held out of RL training (see attacker_env.py's holdout_moves), but that is
about what the trained policy is allowed to learn from, not whether the move
itself works. This script checks the move implementation, independent of
training, so all 7 are exercised here.

It will ask you to confirm before it sends anything, and again before moves
that actuate hardware (cmd_inject, lock_replay, and the legitimate-unlock
setup step for lock_replay) because those move the real servo or relay.
Press Ctrl+C or create a file named LIVE_STOP in this folder at any time to
stop it immediately.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_adapter import LiveBroker  # noqa: E402
from moves import AttackerState, AttackerView, MOVES, execute  # noqa: E402

# (move index, p1, p2, needs_confirmation, what it should visibly do)
PLAN = [
    (1, 0.4, 0.0, False, "temp_spoof: dashboard should briefly show a false ~26 C reading"),
    (2, 0.9, 0.0, False, "const_bias: dashboard should show real temp + ~8 C constant offset"),
    (3, 0.3, 1.0, False, "gradual_drift: dashboard should show real temp + a small rising offset "
                          "(~+0.2 C this one tick -- call this a few times in a row to see it grow)"),
    (4, 1.0, 0.0, False, "pir_freeze: one forged motion=true change"),
    (4, 0.0, 0.0, False, "pir_freeze: one forged motion=false change"),
    (7, 1.0, 0.0, False, "pir_spoof: one forged motion change (fails harmlessly if motion is "
                          "already true -- rerun if so)"),
    (6, 0.0, 0.0, True,  "cmd_inject: unlock — the SERVO WILL MOVE and the door will open"),
    (6, 0.0, 1.0, True,  "cmd_inject: lock — the servo will move back"),
    (6, 1.0, 0.0, True,  "cmd_inject: fan safe_mode — the fan/relay will actuate"),
    (5, 0.0, 0.0, False, "lock_replay: re-sends the legitimate unlock captured just above — "
                          "the SERVO WILL MOVE again"),
]


def confirm(msg: str) -> bool:
    return input(f"{msg} [y/N] ").strip().lower() == "y"


def main() -> None:
    print("This sends real MQTT messages to YOUR broker and can move the real servo.")
    if not confirm("Continue?"):
        return

    with LiveBroker() as env:
        view, state = AttackerView(), AttackerState()

        print("\nSetup for lock_replay: sending a LEGITIMATE unlock+lock cycle (origin=operator) "
              "so there's a real command to capture and replay later.")
        if confirm("  send the legitimate unlock+lock setup? (SERVO WILL MOVE)"):
            env.deliver_command("lock_1", {"action": "unlock", "source": "dashboard"}, origin="operator")
            time.sleep(2.0)
            env.deliver_command("lock_1", {"action": "lock", "source": "dashboard"}, origin="operator")
            time.sleep(2.0)
            view.update(env.log[-10:])
        else:
            print("  skipped -- lock_replay below will report 'nothing captured yet' and do nothing")

        for move, p1, p2, needs_ok, note in PLAN:
            print(f"\n{MOVES[move]} (p1={p1}, p2={p2}) — {note}")
            if needs_ok and not confirm("  send this one?"):
                print("  skipped")
                continue
            try:
                events, valid = execute(env, view, state, move, p1, p2)
            except Exception as e:
                print(f"  FAILED: {e}")
                continue
            if not valid:
                print("  move was invalid here (e.g. nothing captured yet, or value already matches) — nothing sent")
                continue
            for e in events:
                print(f"  sent -> {e.topic}: {e.payload}")
            time.sleep(3.0)          # give the device a moment to react and publish back
            recent_telem = [e for e in env.log[-5:] if e.is_telemetry]
            for e in recent_telem:
                print(f"  device replied <- {e.topic}: {e.payload['data']}")
            view.update(env.log[-5:])

    print("\nDone. Check the dashboard / event log against what's printed above.")


if __name__ == "__main__":
    main()