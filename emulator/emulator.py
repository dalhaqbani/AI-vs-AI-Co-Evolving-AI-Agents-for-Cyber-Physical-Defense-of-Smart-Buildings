"""Publish telemetry for the 5 real smart-building components.

Stands in for Sarah's Arduino firmware until real hardware is wired up and
publishing on its own. Same topics, same payload shape as the real thing,
so nothing downstream (backend, database, dashboard) needs to change when
you swap this container out for the real device.

Once real hardware starts publishing a given component, EXCLUDE that
component here so both don't publish to the same topic at once. Two
publishers on one topic doesn't error, MQTT allows it, it just interleaves
real and fake readings with no way to tell them apart downstream. Example:
real temp_1 is live, the other 4 are still emulated:

    EXCLUDE_COMPONENTS=temp_1 docker compose up emulator
"""

import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "2"))
ANOMALY_EVERY = int(os.getenv("ANOMALY_EVERY", "12"))
EXCLUDE_COMPONENTS = {
    c.strip() for c in os.getenv("EXCLUDE_COMPONENTS", "").split(",") if c.strip()
}

ALL_COMPONENTS = [
    {"component_id": "temp_1", "component_type": "dht22"},
    {"component_id": "motion_1", "component_type": "pir"},
    {"component_id": "lock_1", "component_type": "servo_lock"},
    {"component_id": "fan_1", "component_type": "fan_led"},
    {"component_id": "button_1", "component_type": "push_button"},
]
COMPONENTS = [c for c in ALL_COMPONENTS if c["component_id"] not in EXCLUDE_COMPONENTS]

if EXCLUDE_COMPONENTS:
    skipped = EXCLUDE_COMPONENTS & {c["component_id"] for c in ALL_COMPONENTS}
    unknown = EXCLUDE_COMPONENTS - skipped
    print(f"Excluding from emulation (handled by real hardware): {sorted(skipped) or 'none matched'}")
    if unknown:
        print(f"WARNING: EXCLUDE_COMPONENTS listed unknown ids, ignored: {sorted(unknown)}")

# Actuators are stateful, so we track their last state here rather than
# generating a fresh random value every cycle. This is what "state" means
# for a lock or a fan: it only changes when something acts on it.
_lock_state = "locked"


def make_data(component_type: str, cycle: int, is_test: bool) -> dict:
    global _lock_state

    if component_type == "dht22":
        return {
            "temperature": 45.0 if is_test else round(random.gauss(24.0, 0.8), 1),
            "humidity": round(random.gauss(48.0, 3.0), 1),
        }

    if component_type == "pir":
        return {"motion": True if is_test else random.random() < 0.2}

    if component_type == "servo_lock":
        if is_test:
            _lock_state = "unlocked"  # simulated unauthorized unlock event
        return {"state": _lock_state}

    if component_type == "fan_led":
        return {"state": "on" if (cycle % 6 < 3) else "off"}

    if component_type == "push_button":
        return {"state": "pressed" if random.random() < 0.1 else "released"}

    raise ValueError(f"Unknown component_type: {component_type}")


def make_reading(component: dict, cycle: int) -> dict:
    is_test = cycle > 0 and cycle % ANOMALY_EVERY == 0
    return {
        "component_id": component["component_id"],
        "component_type": component["component_type"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_test": is_test,
        "data": make_data(component["component_type"], cycle, is_test),
    }


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe("building/+/commands")
        names = ', '.join(c['component_id'] for c in COMPONENTS) or "none, all excluded"
        print(f"Emulating {len(COMPONENTS)} components: {names}")


def on_message(client, userdata, message):
    print(f"COMMAND {message.topic}: {message.payload.decode()}")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="emulator")
client.on_connect = on_connect
client.on_message = on_message

while True:
    try:
        client.connect(MQTT_HOST, MQTT_PORT)
        break
    except OSError:
        print("Waiting for MQTT broker...")
        time.sleep(2)

client.loop_start()

try:
    cycle = 0
    while True:
        for index, component in enumerate(COMPONENTS):
            # Offset the anomaly check per component index so the injected
            # test event rotates across all 5 components instead of always
            # hitting the first one in the list.
            payload = make_reading(component, cycle + index)
            topic = f'building/{payload["component_id"]}/telemetry'
            client.publish(topic, json.dumps(payload))
            marker = "TEST" if payload["is_test"] else "DATA"
            print(f"{marker} {topic}: {payload}")
        cycle += 1
        time.sleep(PUBLISH_INTERVAL)
finally:
    client.loop_stop()
    client.disconnect()
