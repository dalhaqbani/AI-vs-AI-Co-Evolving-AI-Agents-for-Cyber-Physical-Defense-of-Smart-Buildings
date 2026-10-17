"""The attacker's moves 


  1 temp_spoof     forge a temperature reading with a chosen value
  2 const_bias     forge real temperature + a fixed offset
  3 gradual_drift  forge real temperature + an offset that grows every tick
  4 pir_freeze     keep publishing one stuck motion value
  5 lock_replay    re-send a captured, legitimate unlock command
  6 cmd_inject     send an unauthorized lock/unlock or fan safe_mode command
  7 pir_spoof      forge ONE motion change (like the real PIR, only on a change)

Move 0 is "noop" (wait), which lets the agent learn stealth.
"""

from __future__ import annotations

from dataclasses import dataclass

from surrogate import BuildingSurrogate, Event

MOVES = ["noop", "temp_spoof", "const_bias", "gradual_drift",
         "pir_freeze", "lock_replay", "cmd_inject", "pir_spoof"]

SPOOF_RANGE_C = (10.0, 50.0)     # value a spoof can claim
BIAS_MAX_C = 10.0                # +/- offset of a constant bias
DRIFT_MIN_C, DRIFT_MAX_C = 0.1, 0.5   # drift added per tick


class AttackerView:
    """What the attacker has learned from sniffing the broker."""

    def __init__(self):
        self.real_temp: float | None = None       # last DEVICE temperature seen
        self.real_humidity: float | None = None
        self.stream_last: dict[str, dict] = {}    # last data per component, any origin

    def update(self, events: list[Event]) -> None:
        for e in events:
            if not e.is_telemetry:
                continue
            self.stream_last[e.component_id] = e.payload["data"]
            # The attacker knows which messages are its own, so it can ignore them
            if e.origin == "device" and e.component_id == "temp_1":
                self.real_temp = e.payload["data"]["temperature"]
                self.real_humidity = e.payload["data"]["humidity"]


@dataclass
class AttackerState:
    prev_move: int = 0
    drift_offset: float = 0.0


def captured_unlock_commands(env: BuildingSurrogate) -> list[dict]:
    """Legitimate unlock commands visible on the broker, oldest first."""
    cmds = env.recent("building/lock_1/commands", n=50)
    return [c.payload for c in cmds
            if c.origin != "attacker" and c.payload.get("action") == "unlock"]


def execute(env: BuildingSurrogate, view: AttackerView, state: AttackerState,
            move: int, p1: float, p2: float) -> tuple[list[Event], bool]:
    """Run one move for one tick. Returns (events produced, move_was_valid)."""
    events: list[Event] = []
    valid = True

    if move != 3:
        state.drift_offset = 0.0        # a drift restarts from zero after any other move

    if move == 0:                                              # noop
        pass

    elif move == 1:                                            # temp_spoof
        value = SPOOF_RANGE_C[0] + p1 * (SPOOF_RANGE_C[1] - SPOOF_RANGE_C[0])
        hum = view.real_humidity if view.real_humidity is not None else 48.0
        events.append(env.inject_telemetry(
            "temp_1", {"temperature": round(value, 1), "humidity": round(hum, 1)}))

    elif move in (2, 3):                                       # const_bias / gradual_drift
        if view.real_temp is None:
            valid = False
        else:
            if move == 2:
                offset = (p1 * 2 - 1) * BIAS_MAX_C
            else:
                step = DRIFT_MIN_C + p1 * (DRIFT_MAX_C - DRIFT_MIN_C)
                state.drift_offset += step if p2 >= 0.5 else -step
                offset = state.drift_offset
            hum = view.real_humidity if view.real_humidity is not None else 48.0
            events.append(env.inject_telemetry(
                "temp_1", {"temperature": round(view.real_temp + offset, 1),
                           "humidity": round(hum, 1)}))

    elif move == 4:                                            # pir_freeze
        events.append(env.inject_telemetry("motion_1", {"motion": bool(p1 >= 0.5)}))

    elif move == 5:                                            # lock_replay
        captured = captured_unlock_commands(env)
        if not captured:
            valid = False
        else:
            payload = dict(captured[int(p1 * (len(captured) - 1))])
            events += env.deliver_command("lock_1", payload, origin="attacker")

    elif move == 6:                                            # cmd_inject
        if p1 < 0.5:
            action = "unlock" if p2 < 0.5 else "lock"
            events += env.deliver_command(
                "lock_1", {"action": action, "source": "dashboard"}, origin="attacker")
        else:
            events += env.deliver_command(
                "fan_1", {"action": "safe_mode", "source": "dashboard"}, origin="attacker")

    elif move == 7:                                            # pir_spoof
        want = bool(p1 >= 0.5)
        current = bool(view.stream_last.get("motion_1", {}).get("motion", False))
        if want == current:
            valid = False                       # the real PIR never repeats a value
        else:
            events.append(env.inject_telemetry("motion_1", {"motion": want}))

    else:
        raise ValueError(f"unknown move {move}")

    state.prev_move = move
    return events, valid
