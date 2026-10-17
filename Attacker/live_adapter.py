"""Live adapter: same interface as BuildingSurrogate, but talks to the real broker.

Attacker code (moves.py) is built only on inject_telemetry(), deliver_command(),
recent(), and .log -- exactly the same things BuildingSurrogate exposes. This
class implements that same interface over a real MQTT connection, so
run_live_smoke_test.py (and later the attacker agent itself) can run unchanged
against real hardware instead of the simulator.

Credentials come from .env (MQTT_USERNAME, MQTT_PASSWORD) at the repo root.
Broker host/port default to localhost:1883 (matching docker-compose) --
override with MQTT_HOST / MQTT_PORT in .env if needed.

Use as a context manager so the connection is cleanly closed on exit:
    with LiveBroker() as env:
        ...
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from surrogate import COMPONENT_TYPES, Event

TOPIC_PREFIX = "building"


class LiveBroker:
    def __init__(self, host: str | None = None, port: int | None = None, timeout: float = 5.0):
        self.host = host or os.environ.get("MQTT_HOST", "localhost")
        self.port = port or int(os.environ.get("MQTT_PORT", "1883"))
        user = os.environ.get("MQTT_USERNAME")
        pw = os.environ.get("MQTT_PASSWORD")
        if not user:
            print("WARNING: MQTT_USERNAME not found in environment/.env -- connecting anonymously")

        self.log: list[Event] = []
        self._data_lock = threading.Lock()
        self._just_sent: set[tuple[str, str]] = set()   # (topic, raw json) we just published
        self._connected = False
        self._connect_rc: int | None = None

        self.client = mqtt.Client()
        if user:
            self.client.username_pw_set(user, pw)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.client.connect(self.host, self.port)
        self.client.loop_start()

        deadline = time.time() + timeout
        while self._connect_rc is None and time.time() < deadline:
            time.sleep(0.1)

        if self._connect_rc is None:
            self.client.loop_stop()
            raise ConnectionError(
                f"No response from MQTT broker at {self.host}:{self.port} "
                f"within {timeout}s -- is it running? (check docker compose ps)")
        if self._connect_rc != 0:
            self.client.loop_stop()
            raise ConnectionError(
                f"MQTT broker at {self.host}:{self.port} rejected the connection "
                f"(rc={self._connect_rc}). rc=5 means not authorized -- check "
                f"MQTT_USERNAME/MQTT_PASSWORD in .env.")
        print(f"LiveBroker connected to {self.host}:{self.port}")

    # ------------------------------------------------------- context manager
    def __enter__(self) -> "LiveBroker":
        return self

    def __exit__(self, *exc) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    # ------------------------------------------------------- mqtt callbacks
    def _on_connect(self, client, userdata, flags, rc):
        self._connect_rc = rc
        self._connected = (rc == 0)
        if rc == 0:
            client.subscribe(f"{TOPIC_PREFIX}/#")

    def _on_message(self, client, userdata, msg) -> None:
        text = msg.payload.decode(errors="replace")
        key = (msg.topic, text)
        with self._data_lock:
            if key in self._just_sent:
                self._just_sent.discard(key)   # our own publish echoing back -- already logged
                return
            try:
                payload = json.loads(text)
            except ValueError:
                return
            origin = "device" if msg.topic.endswith("/telemetry") else "operator"
            self.log.append(Event(time.time(), msg.topic, payload, origin))

    # ------------------------------------------------------ API (matches BuildingSurrogate)
    def inject_telemetry(self, component_id: str, data: dict, origin: str = "attacker") -> Event:
        payload = {
            "component_id": component_id,
            "component_type": COMPONENT_TYPES[component_id],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_test": False,
            "data": data,
        }
        topic = f"{TOPIC_PREFIX}/{component_id}/telemetry"
        text = json.dumps(payload)
        ev = Event(time.time(), topic, payload, origin)
        with self._data_lock:
            self.log.append(ev)
            self._just_sent.add((topic, text))
        self.client.publish(topic, text)
        return ev

    def deliver_command(self, component_id: str, payload: dict, origin: str = "attacker") -> list[Event]:
        topic = f"{TOPIC_PREFIX}/{component_id}/commands"
        text = json.dumps(payload)
        ev = Event(time.time(), topic, payload, origin)
        with self._data_lock:
            self.log.append(ev)
            self._just_sent.add((topic, text))
        self.client.publish(topic, text)
        return [ev]

    def recent(self, topic: str, n: int = 10, origin: str | None = None) -> list[Event]:
        with self._data_lock:
            hits = [e for e in self.log if e.topic == topic and (origin is None or e.origin == origin)]
            return list(hits[-n:])