"""PLACEHOLDER defender and physical-harm metric, used only until the real ones exist.

These stand in so the attacker's training loop has something to push against.
They are deliberately simple and are NOT the project's defender or harm metric:

  * PlaceholderDefender mirrors backend/main.py + ai_engine.py today, plus one
    command rule:
      - a temperature above 30 C, or a jump of more than 5 C between consecutive
        readings, is flagged (the jump rule stands in for a physical-consistency
        check); safe_mode is routed to fan_1 and never toggles a fan that is on; once the
        temperature is back under 28 C the defender switches that fan off again
      - an UNLOCK command with no sign of a person nearby (no motion now, none in
        the last 20 s) is flagged and answered with a lock command
      - a safe_mode command to the fan while the last temperature was not hot is
        flagged and undone (the firmware toggles, so the defender toggles it back)
      - a PIR message that repeats the previous value is flagged and ignored
        (the real sensors publish only on a change, so a repeat is a forgery)
    It does not look at anything else (replayed messages, single forged changes).
  * physical_harm() is a stand-in for Zaina's physical-harm metric.

we will replace PlaceholderDefender with the defender in sprint 3 (same process() interface) and
physical_harm with the real metric. Results from this placeholder prove the
training loop runs, not that an attack works against the real defender.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from surrogate import Event

ACTION_TARGET = {"safe_mode": "fan_1", "lock": "lock_1", "unlock": "lock_1"}
HOT_C = 30.0
JUMP_C = 5.0
PRESENCE_WINDOW_S = 20.0


@dataclass
class Verdict:
    flagged: bool = False
    commands: list[tuple[str, dict]] = field(default_factory=list)   # (component_id, payload)


class PlaceholderDefender:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.latest: dict[str, dict] = {}
        self._prev_temp: float | None = None
        self._fan_on_by_me = False        # I switched the fan on in response to heat
        self._pir_now = False
        self._last_pir: bool | None = None
        self._last_presence_t = float("-inf")

    def process(self, event: Event) -> Verdict:
        verdict = Verdict()
        if event.is_telemetry:
            return self._telemetry(event, verdict)
        return self._command(event, verdict)

    # ------------------------------------------------------------ telemetry
    def _telemetry(self, event: Event, verdict: Verdict) -> Verdict:
        cid, data = event.component_id, event.payload["data"]
        self.latest[cid] = data
        ctype = event.payload["component_type"]

        if ctype == "pir":
            value = bool(data["motion"])
            if value == self._last_pir:            # real PIR never repeats a value
                verdict.flagged = True
                return verdict
            self._last_pir = value
            self._pir_now = value
            if value:
                self._last_presence_t = event.t
        elif ctype == "dht22":
            temp = data["temperature"]
            jumped = self._prev_temp is not None and abs(temp - self._prev_temp) > JUMP_C
            hot = temp > HOT_C
            self._prev_temp = temp
            verdict.flagged = hot or jumped
            target = ACTION_TARGET["safe_mode"]
            fan_state = self.latest.get(target, {}).get("state")
            if hot:
                if fan_state != "on":                                  # firmware toggles
                    self._fan_on_by_me = True
                    verdict.commands.append(
                        (target, {"action": "safe_mode", "reason": f"High temperature: {temp}",
                                  "source": "ai_engine"}))
            elif self._fan_on_by_me and temp <= HOT_C - 2.0 and fan_state == "on":
                self._fan_on_by_me = False                             # heat is gone: undo it
                verdict.commands.append(
                    (target, {"action": "safe_mode", "reason": "Temperature back to normal",
                              "source": "ai_engine"}))
        return verdict

    # ------------------------------------------------------------- commands
    def _command(self, event: Event, verdict: Verdict) -> Verdict:
        if event.component_id == "lock_1" and event.payload.get("action") == "unlock":
            present = event.t - self._last_presence_t <= PRESENCE_WINDOW_S   # a latched 'motion' expires
            if not present:
                verdict.flagged = True
                verdict.commands.append(
                    ("lock_1", {"action": "lock", "reason": "Unlock with no one present",
                                "source": "ai_engine"}))
        elif event.component_id == "fan_1" and event.payload.get("action") == "safe_mode":
            hot = self._prev_temp is not None and self._prev_temp > HOT_C
            if not hot:                       # nothing justifies safe mode: undo the toggle
                verdict.flagged = True
                verdict.commands.append(
                    ("fan_1", {"action": "safe_mode", "reason": "Safe mode with no heat",
                               "source": "ai_engine"}))
        return verdict


def physical_harm(truth: dict, ambient_c: float = 24.0) -> float:
    """Placeholder harm per tick, from hidden physical truth.

      door unlocked while the room is empty   1.0
      fan running although the room is cool   0.3
      temperature more than 1.5 C off ambient 0.5 per extra degree

    The environment rewards only the part the attacker CAUSED: this value in the
    attacked world minus the same value in a no-attacker shadow world.
    """
    harm = 0.0
    if not truth["locked"] and not truth["occupied"]:
        harm += 1.0
    if truth["fan_on"] and truth["temp_c"] <= ambient_c + 0.5:
        harm += 0.3
    harm += 0.5 * max(0.0, abs(truth["temp_c"] - ambient_c) - 1.5)
    return harm
