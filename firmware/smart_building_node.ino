/*
  Smart-Building Sensor Node — ESP32 firmware
  ============================================
  Publishes telemetry that matches backend/main.py's Pydantic schema
  EXACTLY: same field names, same literal strings for component_type and
  state values. If these drift from main.py, the backend silently drops
  the message and logs it, it does not throw a visible error. Check the
  backend's console output the first time you flash this.

  ASSUMPTION, confirm before treating this as final:
  This assumes ONE ESP32 dev board drives all 5 components directly
  (it has built-in Wi-Fi, unlike a plain Arduino Uno). If your actual
  hardware is an Uno talking to a separate ESP8266/ESP32 Wi-Fi module over
  serial, this needs restructuring, tell Claude/your team and it gets
  rewritten around that architecture instead.

  Libraries required (Arduino IDE: Sketch > Include Library > Manage Libraries):
    - PubSubClient        by Nick O'Leary
    - DHT sensor library  by Adafruit
    - Adafruit Unified Sensor   (auto-installed as a dependency of the above)
    - ESP32Servo          by Kevin Harrington / madhephaestus
      (NOT the built-in "Servo" library, that one does not work reliably
      with the ESP32's timers)

  Board setting: Tools > Board > ESP32 Arduino > (your specific ESP32 board)

  Setup: copy config.h.example (same folder as this .ino) to config.h and
  fill in your real Wi-Fi and broker details. config.h is gitignored, so it
  never gets committed, keep it that way.
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ESP32Servo.h>
#include <time.h>
#include "config.h"   // WIFI_SSID, WIFI_PASSWORD, MQTT_HOST, MQTT_PORT — copy config.h.example to config.h and fill in real values, config.h is gitignored on purpose

// ---------------------------------------------------------------------------
// Pin map. Adjust to match your actual wiring.
// ---------------------------------------------------------------------------
const int PIN_DHT22       = 4;
const int PIN_PIR         = 5;
const int PIN_SERVO_LOCK  = 18;
const int PIN_FAN_LED     = 19;
const int PIN_PUSH_BUTTON = 21;

// ---------------------------------------------------------------------------
// Component IDs. These must match component_id values the backend and
// dashboard expect: temp_1, motion_1, lock_1, fan_1, button_1.
// ---------------------------------------------------------------------------
const char* ID_DHT22  = "temp_1";
const char* ID_PIR    = "motion_1";
const char* ID_LOCK   = "lock_1";
const char* ID_FAN    = "fan_1";
const char* ID_BUTTON = "button_1";

const unsigned long PUBLISH_INTERVAL_MS = 2000;
const unsigned long BUTTON_DEBOUNCE_MS  = 50;

DHT dht(PIN_DHT22, DHT22);
Servo lockServo;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

bool lockIsLocked = true;       // servo angle 0 = locked, 90 = unlocked; adjust to your hardware
bool fanIsOn = false;
bool lastButtonReading = HIGH;  // INPUT_PULLUP: idle = HIGH, pressed = LOW
bool buttonState = false;       // debounced state, true = pressed
unsigned long lastDebounceTime = 0;
unsigned long lastPublish = 0;

// ---------------------------------------------------------------------------
// Timestamp: ESP32 has no real-time clock of its own, so we sync over NTP
// in connectWifi() and read the synced time here. Format matches what
// main.py expects: "2026-09-10T12:00:00+00:00".
// ---------------------------------------------------------------------------
String isoTimestamp() {
  time_t now;
  time(&now);
  struct tm timeinfo;
  gmtime_r(&now, &timeinfo);
  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+00:00", &timeinfo);
  return String(buf);
}

// ---------------------------------------------------------------------------
// Manual JSON building instead of ArduinoJson. This skips a second library
// and its heap overhead, worth avoiding once Wi-Fi, MQTT, and a servo are
// all running on the same board at once. Every payload below must match
// the corresponding Pydantic model in backend/main.py field for field.
// ---------------------------------------------------------------------------
void publish(const char* componentId, const char* payload) {
  char topic[64];
  snprintf(topic, sizeof(topic), "building/%s/telemetry", componentId);
  mqtt.publish(topic, payload);
  Serial.print("PUB ");
  Serial.print(topic);
  Serial.print(" ");
  Serial.println(payload);
}

void publishDht22(float temperature, float humidity) {
  char payload[220];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"dht22\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"temperature\":%.1f,\"humidity\":%.1f}}",
    ID_DHT22, isoTimestamp().c_str(), temperature, humidity);
  publish(ID_DHT22, payload);
}

void publishPir(bool motionDetected) {
  char payload[200];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"pir\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"motion\":%s}}",
    ID_PIR, isoTimestamp().c_str(), motionDetected ? "true" : "false");
  publish(ID_PIR, payload);
}

void publishLock(bool locked) {
  char payload[200];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"servo_lock\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"state\":\"%s\"}}",
    ID_LOCK, isoTimestamp().c_str(), locked ? "locked" : "unlocked");
  publish(ID_LOCK, payload);
}

void publishFan(bool on) {
  char payload[200];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"fan_led\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"state\":\"%s\"}}",
    ID_FAN, isoTimestamp().c_str(), on ? "on" : "off");
  publish(ID_FAN, payload);
}

void publishButton(bool pressed) {
  char payload[200];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"push_button\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"state\":\"%s\"}}",
    ID_BUTTON, isoTimestamp().c_str(), pressed ? "pressed" : "released");
  publish(ID_BUTTON, payload);
}

// ---------------------------------------------------------------------------
// Command handling. backend/main.py's Command model sends
// {"action": "...", "reason": "...", "source": "..."} on
// building/{component_id}/commands. This node only reacts to commands
// addressed to its own lock and fan, the two components it can actuate.
// ---------------------------------------------------------------------------
void onMqttMessage(char* topic, byte* payloadBytes, unsigned int length) {
  char payload[256];
  unsigned int copyLen = length < sizeof(payload) - 1 ? length : sizeof(payload) - 1;
  memcpy(payload, payloadBytes, copyLen);
  payload[copyLen] = '\0';

  String topicStr(topic);
  String payloadStr(payload);
  Serial.print("CMD ");
  Serial.print(topicStr);
  Serial.print(" ");
  Serial.println(payloadStr);

  String lockTopic = String("building/") + ID_LOCK + "/commands";
  String fanTopic  = String("building/") + ID_FAN + "/commands";

  if (topicStr == lockTopic) {
    if (payloadStr.indexOf("\"unlock\"") >= 0) {
      lockServo.write(90);
      lockIsLocked = false;
      publishLock(false);
    } else if (payloadStr.indexOf("\"lock\"") >= 0) {
      lockServo.write(0);
      lockIsLocked = true;
      publishLock(true);
    }
  }

  if (topicStr == fanTopic) {
    if (payloadStr.indexOf("\"safe_mode\"") >= 0) {
      digitalWrite(PIN_FAN_LED, LOW);
      fanIsOn = false;
      publishFan(false);
    } else if (payloadStr.indexOf("\"normal_mode\"") >= 0) {
      digitalWrite(PIN_FAN_LED, HIGH);
      fanIsOn = true;
      publishFan(true);
    }
  }
}

void connectWifi() {
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" connected");
  Serial.println(WiFi.localIP());

  // NTP sync so isoTimestamp() returns real UTC time instead of 1970.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("Waiting for NTP time sync");
  time_t now = time(nullptr);
  while (now < 100000) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
  }
  Serial.println(" synced");
}

void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to MQTT broker...");
    String clientId = "esp32-node-" + String(random(0xffff), HEX);
    if (mqtt.connect(clientId.c_str())) {
      Serial.println(" connected");
      mqtt.subscribe((String("building/") + ID_LOCK + "/commands").c_str());
      mqtt.subscribe((String("building/") + ID_FAN + "/commands").c_str());
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqtt.state());
      Serial.println(" retrying in 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_FAN_LED, OUTPUT);
  pinMode(PIN_PUSH_BUTTON, INPUT_PULLUP);
  dht.begin();
  lockServo.attach(PIN_SERVO_LOCK);
  lockServo.write(0); // start locked

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  connectMqtt();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();

  // Debounce the button so one physical press doesn't publish a burst of
  // spurious "pressed" events from mechanical switch bounce.
  bool reading = digitalRead(PIN_PUSH_BUTTON) == LOW;
  if (reading != lastButtonReading) {
    lastDebounceTime = millis();
  }
  if (millis() - lastDebounceTime > BUTTON_DEBOUNCE_MS && reading != buttonState) {
    buttonState = reading;
    publishButton(buttonState);
  }
  lastButtonReading = reading;

  unsigned long nowMs = millis();
  if (nowMs - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = nowMs;

    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();
    if (!isnan(humidity) && !isnan(temperature)) {
      publishDht22(temperature, humidity);
    } else {
      Serial.println("DHT22 read failed, skipping this cycle");
    }

    publishPir(digitalRead(PIN_PIR) == HIGH);
    publishLock(lockIsLocked);
    publishFan(fanIsOn);
  }
}
