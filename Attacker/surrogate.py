"""Surrogate model.

A software stand-in for the real Wemos D1 R1 node. It speaks
the exact interface the real pipeline sees, so an attacker trained against it
can later be pointed at the real broker with no changes:

  * Same 5 components, same ids and component_types as the firmware
    (temp_1/dht22, motion_1/pir, lock_1/servo_lock, fan_1/fan_led,
    button_1/push_button).
  * Same topics: building/<id>/telemetry out, building/<id>/commands in.
  * Same payload shape (validated against backend/main.py's pydantic schema in
    test_surrogate.py).
  * Same publish behaviour as final_working_pipeline_with_wifi.ino:
      - DHT22 every 2 s (a read can fail, then nothing is published)
      - PIR and button only on a change (PIR silent for the first 60 s)
      - lock and fan once at boot, then after every command that reaches them
  * Same command semantics as onMqttMessage(): the topic must contain "lock_1"
    or "fan_1", and the payload is matched by SUBSTRING ('"unlock"', '"lock"',
    '"safe_mode"'), exactly like the firmware's indexOf() checks. safe_mode
    toggles the fan. Commands for any other component do nothing.

It also keeps hidden physical truth (real temperature, occupancy, lock, fan)
separate from what is reported on the wire. That gap is what an attack
exploits, and it is what the physical-harm metric will be computed from.

Nothing here knows about the attacker's moves. The only attacker-facing hooks
are inject_telemetry() and deliver_command(); the six move executors
will be built on top of those two.

Time is simulated: step() advances one 2 s firmware tick and never sleeps.

"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

TICK_S = 2.0            # PUBLISH_INTERVAL_MS in the firmware
PIR_WARMUP_S = 60.0     # PIR_WARMUP_MS in the firmware
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

COMPONENT_TYPES = {
    "temp_1": "dht22",
    "motion_1": "pir",
    "lock_1": "servo_lock",
    "fan_1": "fan_led",
    "button_1": "push_button",
}


@dataclass
class SurrogateConfig:
    """Placeholder parameters. Replace with values fitted from real logs."""

    # Thermal model (per 2 s tick)
    ambient_c: float = 24.0
    thermal_rate: float = 0.05          # fraction of the gap to ambient closed per tick
    occupant_heat_c: float = 0.02       # heating per tick while someone is in the room
    fan_cooling_c: float = 0.06         # cooling per tick while the fan is on
    process_noise_c: float = 0.05
    # DHT22 sensor
    temp_sensor_noise_c: float = 0.3
    ambient_humidity: float = 48.0
    humidity_rate: float = 0.05
    humidity_process_noise: float = 0.3
    humidity_sensor_noise: float = 0.5
    p_dht_fail: float = 0.005           # "DHT22 read failed" -> no publish
    # Occupancy
    p_arrive: float = 0.01
    p_leave: float = 0.03
    # PIR
    pir_detect_prob: float = 0.9
    pir_false_positive: float = 0.005
    pir_hold_ticks: int = 2             # output stays high a few seconds
    # Human behaviour while the room is occupied (per tick)
    p_button: float = 0.02
    p_operator_command: float = 0.005   # a person using the dashboard lock/unlock
    # Start with the PIR already warmed up
    skip_pir_warmup: bool = False


@dataclass(frozen=True)
class Event:
    """One message on the (simulated) broker.

    payload is exactly what would be JSON-encoded on the wire. origin is
    ground truth for labelling and is NOT part of the payload: "device",
    "operator", "backend" or "attacker".
    """

    t: float
    topic: str
    payload: dict
    origin: str

    @property
    def is_telemetry(self) -> bool:
        return self.topic.endswith("/telemetry")

    @property
    def component_id(self) -> str:
        return self.topic.split("/")[1]

    def payload_json(self) -> str:
        return json.dumps(self.payload)


class BuildingSurrogate:
    def __init__(self, config: SurrogateConfig | None = None, seed: int | None = None):
        self.cfg = config or SurrogateConfig()
        self.reset(seed)

    # ------------------------------------------------------------------ setup
    def reset(self, seed: int | None = None) -> list[Event]:
        """Start a new episode. Returns the boot messages (lock + fan state)."""
        if seed is not None or not hasattr(self, "_rng"):
            self._rng = np.random.default_rng(seed)
        c = self.cfg
        self.t = 0.0
        self.log: list[Event] = []
        # Hidden physical truth
        self.true_temp = float(c.ambient_c + self._rng.normal(0, 0.2))
        self.true_humidity = float(c.ambient_humidity + self._rng.normal(0, 1.0))
        self.occupied = bool(self._rng.random() < 0.3)
        self.locked = True
        self.fan_on = False
        # Device-side state
        self._pir_raw_ticks = 0         # ticks of PIR output left to hold high
        self._pir_last = False
        self._pir_warmed = c.skip_pir_warmup
        self._button_down = False
        boot = [self._lock_event("device"), self._fan_event("device")]
        return boot

    def truth(self) -> dict:
        """Hidden physical state. Not visible to the attacker or defender."""
        return {
            "t": self.t,
            "temp_c": self.true_temp,
            "humidity": self.true_humidity,
            "occupied": self.occupied,
            "locked": self.locked,
            "fan_on": self.fan_on,
        }

    # ------------------------------------------------------------- time step
    def step(self) -> list[Event]:
        """Advance one 2 s firmware tick. Returns the events of this tick."""
        c, rng = self.cfg, self._rng
        self.t += TICK_S
        out: list[Event] = []

        # 1. People come and go
        if self.occupied:
            if rng.random() < c.p_leave:
                self.occupied = False
        elif rng.random() < c.p_arrive:
            self.occupied = True

        # 2. A person in the room may use the dashboard lock, or press the button
        if self.occupied and rng.random() < c.p_operator_command:
            action = "unlock" if self.locked else "lock"
            out += self.deliver_command(
                "lock_1", {"action": action, "source": "dashboard"}, origin="operator"
            )
        if self._button_down:
            self._button_down = False
            out.append(self._button_event(False))
        elif self.occupied and rng.random() < c.p_button:
            self._button_down = True
            out.append(self._button_event(True))

        # 3. Physics
        heat = c.occupant_heat_c if self.occupied else 0.0
        cool = c.fan_cooling_c if self.fan_on else 0.0
        self.true_temp += (
            c.thermal_rate * (c.ambient_c - self.true_temp)
            + heat - cool + rng.normal(0, c.process_noise_c)
        )
        self.true_humidity += (
            c.humidity_rate * (c.ambient_humidity - self.true_humidity)
            + rng.normal(0, c.humidity_process_noise)
        )

        # 4. PIR: publishes on change only, and only after the 60 s warm-up
        detected = (
            rng.random() < c.pir_detect_prob if self.occupied
            else rng.random() < c.pir_false_positive
        )
        if detected:
            self._pir_raw_ticks = c.pir_hold_ticks
        pir_now = self._pir_raw_ticks > 0
        if self._pir_raw_ticks > 0:
            self._pir_raw_ticks -= 1
        if not self._pir_warmed:
            if self.t >= PIR_WARMUP_S:
                self._pir_warmed = True
                self._pir_last = pir_now        # firmware records state, no publish
        elif pir_now != self._pir_last:
            self._pir_last = pir_now
            out.append(self._pir_event(pir_now))

        # 5. DHT22: every tick unless the read fails
        if rng.random() >= c.p_dht_fail:
            temp = self.true_temp + rng.normal(0, c.temp_sensor_noise_c)
            hum = self.true_humidity + rng.normal(0, c.humidity_sensor_noise)
            out.append(self._event(
                "temp_1", {"temperature": round(float(temp), 1),
                           "humidity": round(float(hum), 1)}, "device"))
        return out

    # --------------------------------------------------------------- commands
    def deliver_command(self, component_id: str, payload, origin: str = "backend") -> list[Event]:
        """Deliver a command to building/<component_id>/commands.

        Mirrors onMqttMessage() in the firmware, including its substring
        matching. Returns the telemetry the device publishes in response.
        """
        topic = f"building/{component_id}/commands"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        body = payload if isinstance(payload, dict) else {"raw": text}
        self.log.append(Event(self.t, topic, body, origin))

        out: list[Event] = []
        if "lock_1" in topic:
            if '"unlock"' in text:
                self.locked = False
                out.append(self._lock_event("device"))
            elif '"lock"' in text:
                self.locked = True
                out.append(self._lock_event("device"))
        elif "fan_1" in topic:
            if '"safe_mode"' in text:
                self.fan_on = not self.fan_on      # toggle, like setFan(!fanOn)
                out.append(self._fan_event("device"))
        return out

    # ---------------------------------------------------------------- attacks
    def inject_telemetry(self, component_id: str, data: dict, origin: str = "attacker") -> Event:
        """Publish a forged reading as if it came from component_id.

        Changes nothing physical. The harm comes later, when the backend or a
        person reacts to the false reading.
        """
        return self._event(component_id, data, origin)

    def recent(self, topic: str, n: int = 10, origin: str | None = None) -> list[Event]:
        """Last n events on a topic (what a sniffing attacker could capture)."""
        hits = [e for e in self.log if e.topic == topic and (origin is None or e.origin == origin)]
        return hits[-n:]

    # ---------------------------------------------------------------- helpers
    def _iso(self) -> str:
        return (EPOCH + timedelta(seconds=self.t)).isoformat()

    def _event(self, component_id: str, data: dict, origin: str) -> Event:
        payload = {
            "component_id": component_id,
            "component_type": COMPONENT_TYPES[component_id],
            "timestamp": self._iso(),
            "is_test": False,
            "data": data,
        }
        ev = Event(self.t, f"building/{component_id}/telemetry", payload, origin)
        self.log.append(ev)
        return ev

    def _lock_event(self, origin: str) -> Event:
        return self._event("lock_1", {"state": "locked" if self.locked else "unlocked"}, origin)

    def _fan_event(self, origin: str) -> Event:
        return self._event("fan_1", {"state": "on" if self.fan_on else "off"}, origin)

    def _button_event(self, pressed: bool) -> Event:
        return self._event("button_1", {"state": "pressed" if pressed else "released"}, "device")

    def _pir_event(self, motion: bool) -> Event:
        return self._event("motion_1", {"motion": motion}, "device")
