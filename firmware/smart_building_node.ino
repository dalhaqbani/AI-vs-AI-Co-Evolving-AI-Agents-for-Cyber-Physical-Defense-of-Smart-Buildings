#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <Servo.h>
#include <time.h>
#include "config.h"

const int PIN_DHT22       = D5;
const int PIN_PUSH_BUTTON = D7;
const int PIN_PIR         = D2;
const int PIN_SERVO_LOCK  = D6;
const int PIN_FAN         = D1;

const char* ID_DHT22  = "temp_1";
const char* ID_BUTTON = "button_1";
const char* ID_PIR    = "motion_1";
const char* ID_LOCK   = "lock_1";
const char* ID_FAN    = "fan_1";

const unsigned long PUBLISH_INTERVAL_MS = 2000;
const unsigned long BUTTON_DEBOUNCE_MS  = 50;

DHTesp dht;
Servo lockServo;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

unsigned long lastPublish = 0;
bool lastButtonReading = HIGH;  // INPUT_PULLUP: idle = HIGH, pressed = LOW
bool buttonState = false;
unsigned long lastDebounceTime = 0;
bool lastPirState = false;
bool isLocked = true;   // matches lockServo.write(0) at boot
bool fanOn = false;     // matches setFan(false) at boot

String isoTimestamp() {
  time_t now;
  time(&now);
  struct tm timeinfo;
  gmtime_r(&now, &timeinfo);
  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+00:00", &timeinfo);
  return String(buf);
}

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

void publishButton(bool pressed) {
  char payload[200];
  snprintf(payload, sizeof(payload),
    "{\"component_id\":\"%s\",\"component_type\":\"push_button\",\"timestamp\":\"%s\","
    "\"is_test\":false,\"data\":{\"state\":\"%s\"}}",
    ID_BUTTON, isoTimestamp().c_str(), pressed ? "pressed" : "released");
  publish(ID_BUTTON, payload);
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

// Handles the NC-inverted relay logic. Always call this instead of
// digitalWrite(PIN_FAN, ...) directly.
void setFan(bool on) {
  fanOn = on;
  digitalWrite(PIN_FAN, on ? LOW : HIGH);
  publishFan(on);
}

void onMqttMessage(char* topic, byte* payloadBytes, unsigned int length) {
  char payload[256];
  unsigned int copyLen = length < sizeof(payload) - 1 ? length : sizeof(payload) - 1;
  memcpy(payload, payloadBytes, copyLen);
  payload[copyLen] = '\0';

  Serial.print("CMD received on ");
  Serial.print(topic);
  Serial.print(": ");
  Serial.println(payload);

  String topicStr(topic);
  String payloadStr(payload);

  if (topicStr.indexOf("lock_1") >= 0) {
    if (payloadStr.indexOf("\"unlock\"") >= 0) {
      Serial.println("-> moving servo to 90 (unlock)");
      lockServo.write(90);
      isLocked = false;
      publishLock(isLocked);
    } else if (payloadStr.indexOf("\"lock\"") >= 0) {
      Serial.println("-> moving servo to 0 (lock)");
      lockServo.write(0);
      isLocked = true;
      publishLock(isLocked);
    } else {
      Serial.println("-> payload didn't match \"unlock\" or \"lock\", no action");
    }
  } else if (topicStr.indexOf("fan_1") >= 0) {
    if (payloadStr.indexOf("\"normal_mode\"") >= 0) {
      Serial.println("-> Fan ON");
      setFan(true);
    } else if (payloadStr.indexOf("\"safe_mode\"") >= 0) {
      Serial.println("-> Fan OFF");
      setFan(false);
    } else {
      Serial.println("-> payload didn't match \"normal_mode\" or \"safe_mode\", no action");
    }
  }
}

void connectWifi() {
  WiFi.setOutputPower(0);  // lowers WiFi TX power to reduce the current spike at connect
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" connected");
  Serial.println(WiFi.localIP());

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
    String clientId = "smart-building-full-" + String(random(0xffff), HEX);
     if (mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD)) {
      Serial.println(" connected");

      String lockTopic = String("building/") + ID_LOCK + "/commands";
      mqtt.subscribe(lockTopic.c_str());
      Serial.print("Subscribed to ");
      Serial.println(lockTopic);

      String fanTopic = String("building/") + ID_FAN + "/commands";
      mqtt.subscribe(fanTopic.c_str());
      Serial.print("Subscribed to ");
      Serial.println(fanTopic);
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqtt.state());
      Serial.println(" retrying in 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(9600);

  dht.setup(PIN_DHT22, DHTesp::DHT22);
  pinMode(PIN_PUSH_BUTTON, INPUT_PULLUP);
  pinMode(PIN_PIR, INPUT);
  lockServo.attach(PIN_SERVO_LOCK);
  lockServo.write(0);
  pinMode(PIN_FAN, OUTPUT);
  digitalWrite(PIN_FAN, HIGH);  // energized -> NC open -> fan OFF at boot

  Serial.println("DHT22 D5, button D7, PIR D2, servo D6, fan D1 (NC logic) - all ready");

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  connectMqtt();

  // Publish initial states so the dashboard isn't stale
  publishLock(isLocked);
  publishFan(fanOn);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();

  // Debounce the button
  bool reading = digitalRead(PIN_PUSH_BUTTON) == LOW;
  if (reading != lastButtonReading) {
    lastDebounceTime = millis();
  }
  if (millis() - lastDebounceTime > BUTTON_DEBOUNCE_MS && reading != buttonState) {
    buttonState = reading;
    publishButton(buttonState);
  }
  lastButtonReading = reading;

  // PIR: publish immediately on change
  bool pirReading = digitalRead(PIN_PIR) == HIGH;
  if (pirReading != lastPirState) {
    lastPirState = pirReading;
    publishPir(pirReading);
  }

  unsigned long nowMs = millis();
  if (nowMs - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = nowMs;

    float humidity = dht.getHumidity();
    float temperature = dht.getTemperature();
    if (dht.getStatus() == 0) {
      publishDht22(temperature, humidity);
    } else {
      Serial.print("DHT22 read failed: ");
      Serial.println(dht.getStatusString());
    }
  }
}
