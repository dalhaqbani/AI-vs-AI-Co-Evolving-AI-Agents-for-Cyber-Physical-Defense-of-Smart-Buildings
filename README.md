<div align="center">

# 🏢 AI vs AI — Co-Evolving AI Agents for Cyber-Physical Defense of Smart Buildings

### A real hardware testbed where an attacker AI and a defender AI continuously adapt to each other.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-Dashboard-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-3C5280?style=flat-square&logo=eclipsemosquitto&logoColor=white)](https://mosquitto.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

</div>

---

## 📖 Overview

**Hisn** (SE499 Capstone — Prince Sultan University) is a senior software engineering project that builds a real smart-building IoT testbed where an **attacker agent** and a **defender agent** co-evolve against each other. Unlike most cyber-physical security research that stops at simulation, every attack here can trigger **measurable physical effects** on real hardware — a locked door actually unlocking, a fan actually turning on, a sensor actually reporting a fake reading.

> **Core claim:** a defender trained through co-evolution against an adaptive attacker generalizes to unseen attacks better than a defender trained on static attack patterns — validated on real hardware, not simulation alone.

---

## 🏗️ Architecture

                    ┌─────────────────────────────┐
                    │      🧠 Defender (planned)    │
                    │  Detect model + Respond RL    │
                    │  (2-layer: network watchdog + │
                    │  physical consistency check)  │
                    └──────────────┬─────────────────┘
                                   │ response commands
                                   ▼
 ┌──────────────────┐      MQTT       ┌──────────────┐      ┌──────────────┐
 │  Physical Testbed  │ ◀───────────  │  MQTT Broker  │ ◀───▶│   Backend    │
 │  (Wemos D1 +       │ ───────────▶  │ (Mosquitto,   │      │ (FastAPI +   │
 │   sensors/actuators)│   telemetry   │  authenticated)│      │  SQLite)     │
 └──────────────────┘                 └──────────────┘      └──────┬───────┘
                                              ▲                     │
                                              │ attack traffic      ▼
                                   ┌──────────┴─────────────┐  ┌──────────────┐
                                   │   😈 Attacker Agent      │  │  Dashboard   │
                                   │  (planned) PPO/RL —      │  │   (React)    │
                                   │  spoofing, replay,       │  └──────────────┘
                                   │  freeze, drift, etc.     │
                                   └──────────────────────────┘



> **Note:** the Attacker and Defender agents are design-stage components at this point in the project. Per the pipeline-first principle, the hardware → MQTT → backend → dashboard pipeline is built and secured first; the AI layers above connect into it once infrastructure is validated.

An **emulator** container stands in for any component not yet wired up in hardware, publishing to the same topics with the same payload shape — nothing downstream needs to change when real hardware comes online.

---

## 🔩 Hardware Components

| Component | ID | Type | Role |
|---|---|---|---|
| 🌡️ DHT22 | `temp_1` | Sensor | Temperature / humidity |
| 🚶 PIR | `motion_1` | Sensor | Motion detection |
| 🔒 Servo lock | `lock_1` | Actuator | Door lock/unlock |
| 🌀 Fan (relay) | `fan_1` | Actuator | Fan on/off |
| 🔘 Push button | `button_1` | Sensor | Manual trigger |

Board: **Wemos D1 (ESP8266)** · Firmware: Arduino (`firmware/`)

---

## 🚀 Setup

### 1. Environment variables
```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```
Open `.env` and set real values for `MQTT_USERNAME` and `MQTT_PASSWORD`.

### 2. MQTT broker credentials
The broker requires authentication (`allow_anonymous false`). Generate its password file — **not committed to git**:
```bash
docker run --rm -v "${PWD}/broker:/mosquitto/config" eclipse-mosquitto:2 sh -c "mosquitto_passwd -c -b /mosquitto/config/passwordfile <username> <password> && chmod 644 /mosquitto/config/passwordfile"
```
Use the same username/password as step 1. If `broker/passwordfile` already exists, delete it first (`mosquitto_passwd -c` won't overwrite).

### 3. Launch the stack
```bash
docker compose up --build
```

| Service | URL |
|---|---|
| 🖥️ Dashboard | http://localhost:5173 |
| ⚙️ Backend API | http://localhost:8000 |
| ❤️ Health check | http://localhost:8000/health |

### 4. Real hardware (optional)
```bash
copy firmware\config.h.example firmware\config.h   # Windows
```
Fill in your real Wi-Fi and MQTT credentials (must match `.env`), then flash to the board via Arduino IDE.

> ⚠️ If real hardware is publishing a component, exclude it from the emulator so both don't publish to the same topic:
> ```bash
> EXCLUDE_COMPONENTS=temp_1 docker compose up emulator
> ```

---

## 🔐 Security

- MQTT broker requires authentication — anonymous connections are rejected
- Credentials never committed: `.env`, `broker/passwordfile`, and `firmware/config.h` are all git-ignored
- Backend validates every incoming reading against a strict per-component schema before it touches the database

---

## 🧪 Testing

Test cases, results, and known issues are documented per sprint — see the sprint documentation for full test tables and the bug tracker for open items.

---


## 📄 License

MIT — see [LICENSE](LICENSE)

<div align="center">

*Prince Sultan University · College of Computer & Information Sciences · SE499*

</div>
