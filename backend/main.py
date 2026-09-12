import json
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ai_engine import evaluate
from database import initialize, insert_reading, latest_per_component, recent


MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TELEMETRY_TOPIC = "building/+/telemetry"
mqtt_connected = False


# ---------------------------------------------------------------------------
# Per-component telemetry schema.
#
# Each of the 5 hardware components publishes its own "data" shape.
# component_type is the discriminator: Pydantic picks the matching model
# automatically, and anything that doesn't match one of these five is
# rejected at the door instead of silently corrupting the database.
#
# Confirm this exact contract with Sarah before she finalizes firmware.
# ---------------------------------------------------------------------------

class DHT22Data(BaseModel):
    temperature: float
    humidity: float


class PIRData(BaseModel):
    motion: bool


class ServoLockData(BaseModel):
    state: Literal["locked", "unlocked"]


class FanLedData(BaseModel):
    state: Literal["on", "off"]


class PushButtonData(BaseModel):
    state: Literal["pressed", "released"]


class DHT22Telemetry(BaseModel):
    component_id: str
    component_type: Literal["dht22"]
    timestamp: str
    is_test: bool = False
    data: DHT22Data


class PIRTelemetry(BaseModel):
    component_id: str
    component_type: Literal["pir"]
    timestamp: str
    is_test: bool = False
    data: PIRData


class ServoLockTelemetry(BaseModel):
    component_id: str
    component_type: Literal["servo_lock"]
    timestamp: str
    is_test: bool = False
    data: ServoLockData


class FanLedTelemetry(BaseModel):
    component_id: str
    component_type: Literal["fan_led"]
    timestamp: str
    is_test: bool = False
    data: FanLedData


class PushButtonTelemetry(BaseModel):
    component_id: str
    component_type: Literal["push_button"]
    timestamp: str
    is_test: bool = False
    data: PushButtonData


Telemetry = Annotated[
    Union[
        DHT22Telemetry,
        PIRTelemetry,
        ServoLockTelemetry,
        FanLedTelemetry,
        PushButtonTelemetry,
    ],
    Field(discriminator="component_type"),
]
telemetry_adapter: TypeAdapter[Telemetry] = TypeAdapter(Telemetry)


class Command(BaseModel):
    component_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    action: str = Field(pattern=r"^(safe_mode|normal_mode|inspect|lock|unlock|reset)$")


def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = reason_code == 0
    if mqtt_connected:
        client.subscribe(TELEMETRY_TOPIC)
        print(f"Subscribed to {TELEMETRY_TOPIC}")
    else:
        print(f"MQTT connection failed: {reason_code}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = False


def on_message(client, userdata, message):
    try:
        reading = telemetry_adapter.validate_json(message.payload)
    except ValidationError as exc:
        # Anything that doesn't match one of the 5 known component shapes
        # is dropped and logged, not stored. This is what keeps a firmware
        # typo from silently corrupting the dataset.
        print(f"Ignored invalid telemetry on {message.topic}: {exc}")
        return

    decision = evaluate(reading.component_type, reading.data.model_dump()).to_dict()
    insert_reading(reading.model_dump(), decision)

    if decision["action"]:
        command_topic = f"building/{reading.component_id}/commands"
        command = json.dumps(
            {
                "action": decision["action"],
                "reason": decision["alert"],
                "source": "ai_engine",
            }
        )
        client.publish(command_topic, command)


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="backend")
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
    yield
    mqtt_client.loop_stop()
    mqtt_client.disconnect()


app = FastAPI(title="Smart-Building Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "mqtt_connected": mqtt_connected}


@app.get("/api/latest")
def latest():
    return latest_per_component()


@app.get("/api/readings")
def readings(limit: int = Query(default=50, ge=1, le=500)):
    return recent(limit)


@app.post("/api/commands")
def send_command(command: Command):
    if not mqtt_connected:
        raise HTTPException(status_code=503, detail="MQTT broker is unavailable")

    topic = f"building/{command.component_id}/commands"
    payload = {"action": command.action, "source": "dashboard"}
    mqtt_client.publish(topic, json.dumps(payload))
    return {"published": True, "topic": topic, "command": payload}
