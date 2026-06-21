#include <Arduino.h>
/*
 * HIGH BEAM DETECTION SYSTEM
 * ESP32 Firmware — Wokwi Simulator (AWS IoT Core TCP/MQTT)
 * 
 * Sensors : LDR/photoresistor (pin 34) + vehicle switch (pin 35)
 * Outputs : Red LED (pin 32), Green LED (pin 33), Buzzer (pin 25), LCD I2C
 */

#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>

// —— AWS IoT Configuration ——————————————————————————————————————
const char* AWS_IOT_ENDPOINT = "a2dsifw7wpw9m4-ats.iot.ap-south-1.amazonaws.com";
const char* THING_NAME       = "HighBeamESP32";
const char* TOPIC_SENSOR     = "highbeam/sensor/data";
const char* TOPIC_VIOLATION  = "highbeam/violations";
const char* TOPIC_CONFIG     = "highbeam/config";

// —— Certificates (Extracted from backend/iot_certs) ——————————————
const char* AWS_CERT_CA = 
"-----BEGIN CERTIFICATE-----\n"
"MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF\n"
"ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6\n"
"b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL\n"
"MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv\n"
"b3QgQ0EgMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj\n"
"ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM\n"
"9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw\n"
"IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6\n"
"VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L\n"
"93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm\n"
"jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC\n"
"AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA\n"
"A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI\n"
"U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs\n"
"N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90vv\n"
"o/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZFo6IQ6XU\n"
"5MsI+yMRQ+hDKXJioaldXgjUkK642M4UwtBV8ob2xJNDd2ZhwLnoQdeXeGADbkpy\n"
"rqXRfboQnoZsG4q5WTP468SQvvG5\n"
"-----END CERTIFICATE-----\n";

const char* AWS_CERT_CRT = 
"-----BEGIN CERTIFICATE-----\n"
"MIIDWTCCAkGgAwIBAgIUf3w4QrJTF+QcBm/prEJnuTVSp9gwDQYJKoZIhvcNAQEL\n"
"BQAwTTFLMEkGA1UECwxCQW1hem9uIFdlYiBTZXJ2aWNlcyBPPUFtYXpvbi5jb20g\n"
"SW5jLiBMPVNlYXR0bGUgU1Q9V2FzaGluZ3RvbiBDPVVTMB4XDTI2MDUxMjE0MjUy\n"
"M1oXDTQ5MTIzMTIzNTk1OVowHjEcMBoGA1UEAwwTQVdTIElvVCBDZXJ0aWZpY2F0\n"
"ZTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKZh3aqbpdWWSYxsHJrp\n"
"1ktxoeTqsGi8TH5fVlu72l5hdwpiJKe2IKTjGh+RA4xMMPepxle9SrhZ2IdFZfc6\n"
"6VM6AjkmHDdUKRrBK0CVCQGVRwSkVfbwFts7LAr3gfMWT/efYySakGcy/7uJ4zor\n"
"F2tUw94/CwRDt/XHm/3VgzdKDOwJkIhEppCH98njt15Yot/EYa0ZOtfICZKQ8ptL\n"
"QXG8+nTwbcoGCVf0i7s9VLQHunTMCMtbizopLtg7gms4sD/ZLKuQjIII2S5PyUgT\n"
"b1bgg/8V5oiDJccjvGWwvm/SCTPJCrZziG0wlXxxXJRIPGmktJE3QRImCJMDTyqy\n"
"nYECAwEAAaNgMF4wHwYDVR0jBBgwFoAUIGQNjQ18nb/jCc3qG91tiDDfK6owHQYD\n"
"VR0OBBYEFGnzaq3EJc2hxMrT5XOUXsd3f/zIMAwGA1UdEwEB/wQCMAAwDgYDVR0P\n"
"AQH/BAQDAgeAMA0GCSqGSIb3DQEBCwUAA4IBAQDC9B9zmshkeIeh/RqCfcSYZ2na\n"
"tPW7v6IFfcm00W5xt9LiJci6wG2GQLzkE6UKor/acgzmHmE46+wtNpFkDs67nRbE\n"
"x44xstc7rxBKMHnkv+bBjoOz4N3U/7VAfLCYQfbRd3hatRp5aaakBSjWy7dcJsLn\n"
"ipj7Nr2Rsgp+GtprkkBcRQS5dONGnVV2Z2qX9e5W6CUI4eiTz5PwQUfoN3lIgFMt\n"
"nrw8l47zyLY6FACO64+Lh83Myl4XXH+r1Xdqz/0Y/SpALVqme7MXV8ciTPVq1Gya\n"
"qLTxBOkFZ4j32T+KNKhKhFWl7COWmiHoNUR1mCyEXTX8jx4pi2vpecJXoZar\n"
"-----END CERTIFICATE-----\n";

const char* AWS_CERT_PRIVATE = 
"-----BEGIN RSA PRIVATE KEY-----\n"
"MIIEowIBAAKCAQEApmHdqpul1ZZJjGwcmunWS3Gh5OqwaLxMfl9WW7vaXmF3CmIk\n"
"p7YgpOMaH5EDjEww96nGV71KuFnYh0Vl9zrpUzoCOSYcN1QpGsErQJUJAZVHBKRV\n"
"9vAW2zssCveB8xZP959jJJqQZzL/u4njOisXa1TD3j8LBEO39ceb/dWDN0oM7AmQ\n"
"iESmkIf3yeO3Xlii38RhrRk618gJkpDym0tBcbz6dPBtygYJV/SLuz1UtAe6dMwI\n"
"y1uLOiku2DuCaziwP9ksq5CMggjZLk/JSBNvVuCD/xXmiIMlxyO8ZbC+b9IJM8kK\n"
"tnOIbTCVfHFclEg8aaS0kTdBEiYIkwNPKrKdgQIDAQABAoIBAEl9Qp5/v0DW78eB\n"
"XSUjkc6i80IcUyz/tr2+uHMuRyaW4DCK5uvY6xRJZxl4QNvwL1TE/WF1r5I7xQMC\n"
"qSYJNFPZdG/voFjj6H/zwHn3GhJD8ClhuMKoiz/sI+j4J8LKISobkXvydUPP59ra\n"
"7a2cqOx0dUsuB1yr2I5Ly1/TtfBuI3GWEw5iQG17tnvPpkPCFyUdRtXGlyklin6w\n"
"XT1WTtwl5I6Ch2M0JXhd5WCmYxvbNBVP6aH1sQ6bLBzMkZ1xhJfr1JrNxvY7p/e2\n"
"OOMiXWJqCa3t+aLVFxXcbrsQVn/FfeFdtm1v2RaoxmcBLBQlpxmA9w1MibVianFN\n"
"P8DSIUECgYEA0G3f22e0qF1dkrLkSPgYLRu8EPxM+ihhmsHBz3bUYLogKog/klnD\n"
"TK+VZzaAYrFPrrA8C5Zhh+KRIF5uxOs4PKOaK3JfzmaAWNH6XkTqjcwzD3t3wNTa\n"
"j0uLvxBMtvqG+nNU1Hwe1TG8KQNl4XBKVweWYnMq8xqXJFkiMW2wCckCgYEAzFtF\n"
"DjlSfsXLMTUwbMlH7M0BUfs5HGbN7zdM8vbeuCfglZ9+Nb6RPuvtAeq8L9pRY2Ve\n"
"Ti5ycnPP9drjppXzZdz/DQz8aEmoH/gq7WbDvURARPYqQPgBZcEQzH5143aRGCdo\n"
"WqvvarfFxt3ikbB34Cw0rhcMJTKx+eeVCGyc0fkCgYBBLXrJU57M35V5YHO+1cZJ\n"
"pNikvyEbQTF0gY6n39L+BHY2lrC6hVNrUaT4x7RSKHxwdi/wt6/8HD+hzaX58kx9\n"
"ufNmDrgjBS6xl8ghXo+yk96LwuJp7KYEFW2369LxjVpUS2iFoeLKbGkYsjVT0BeP\n"
"K5I9ayJNL02AUlc8+b4I8QKBgFtieTgKJDzywJHJwlTzkclwR6XcnUZ5JqBR74Q7\n"
"J4/crU7bmvn3tUYZBMy1puATVhAH1atKk/1gkt/TubfWGJk1wYyZgQo13gwl0zSE\n"
"nwW1TrRIDM8u2JkjRBredmN4sxvFC6J4fsEPW086DEawTnsd6ZTsU48S44noqLmy\n"
"sFIpAoGBAIKOAx76XtGLw1BoUqr81I4bV4Uisis7fR2eLQbVbnExKFZDlg166CP2\n"
"QPRkQagMgrKX/NSgem4/dS26KrbJl7sNIy83oYHgV8rDYy5N+Q/L05GWeRDr2b7L\n"
"9g9bDIGE3+zI6iHBDvk21HhWXEFlFrpFTzQDMX0pxml/qcN8scx2\n"
"-----END RSA PRIVATE KEY-----\n";

// —— Hardware / Pins ——————————————————————————————————————————————
#define LDR_PIN 34
#define VEHICLE_PIN 35
#define RED_LED 32
#define GREEN_LED 33
#define BUZZER_PIN 25

// —— Simulation Constants ————————————————————————————————————————
const char* ssid = "Wokwi-GUEST";
const char* password = "";

// —— Thresholds ——————————————————————————————————————————————————
#define DEFAULT_HIGH_BEAM_LUX 740
#define DEFAULT_NORMAL_BEAM_LUX 300
#define TRIGGER_DURATION 2000

// —— Global Objects ——————————————————————————————————————————————
WiFiClientSecure net;
PubSubClient client(net);
LiquidCrystal_I2C lcd(0x27, 16, 2);

// —— State Variables —————————————————————————————————————————————
int luxValue = 0;
int rawLdrValue = 0;
int highBeamLux = DEFAULT_HIGH_BEAM_LUX;
int normalBeamLux = DEFAULT_NORMAL_BEAM_LUX;
bool vehiclePresent = false;
bool highBeamActive = false;
bool violationSent = false;
unsigned long highBeamStart = 0;
unsigned long lastSend = 0;
unsigned long lastAwsRetry = 0;
int violationCount = 0;

const char* MOCK_PLATES[] = {"TN01AB1234", "TN02CD5678", "TN03EF9012", "MH04GH3456", "KA05IJ7890", "AP06KL2345"};
int plateIndex = 0;

// —— Functions ———————————————————————————————————————————————————

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println(message);

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, message);
  if (!error) {
    highBeamLux = DEFAULT_HIGH_BEAM_LUX;
    if (doc["normal_beam_threshold"].is<int>()) {
      normalBeamLux = doc["normal_beam_threshold"];
    }
    Serial.println("Config Updated via MQTT");
  }
}

bool connectAWS() {
  if (client.connected()) {
    return true;
  }

  Serial.print("Connecting to AWS IoT Core...");
  lcd.clear();
  lcd.print("Connecting AWS");
  
  net.setCACert(AWS_CERT_CA);
  net.setCertificate(AWS_CERT_CRT);
  net.setPrivateKey(AWS_CERT_PRIVATE);
  
  client.setServer(AWS_IOT_ENDPOINT, 8883);
  client.setCallback(mqttCallback);

  for (int attempt = 1; attempt <= 3 && !client.connected(); attempt++) {
    if (client.connect(THING_NAME)) {
      Serial.println("CONNECTED!");
      lcd.clear();
      lcd.print("AWS Connected!");
      client.subscribe(TOPIC_CONFIG);
      return true;
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" continuing with TCP/serial JSON");
      lcd.setCursor(0,1);
      lcd.print("TCP fallback");
      delay(1000);
    }
  }

  Serial.println("AWS IoT unavailable. Sensor data will still stream over Wokwi TCP/serial.");
  return false;
}

void publishMessage(const char* topic, JsonDocument& doc) {
  char buffer[512];
  serializeJson(doc, buffer);

  // Keep this pure JSON line: the Python backend reads it over Wokwi's
  // RFC2217/TCP serial bridge even when AWS MQTT is temporarily offline.
  Serial.println(buffer);

  if (client.connected()) {
    client.publish(topic, buffer);
    Serial.print("Published to ");
    Serial.print(topic);
    Serial.print(": ");
    Serial.println(buffer);
  } else {
    Serial.print("AWS offline, TCP JSON only for ");
    Serial.println(topic);
  }
}

int adcToLux(int raw) {
  const float GAMMA = 0.7;
  const float RL10 = 50.0;
  float voltage = raw / 4095.0 * 3.3;
  voltage = constrain(voltage, 0.01, 3.29);
  float resistance = 2000.0 * voltage / (3.3 - voltage);
  float lux = pow(RL10 * 1000.0 * pow(10.0, GAMMA) / resistance, 1.0 / GAMMA);
  if (isnan(lux) || isinf(lux)) {
    lux = map(raw, 0, 4095, 0, 5000);
  }
  return constrain((int)lux, 0, 5000);
}

void setNormalMode() {
  digitalWrite(RED_LED, LOW);
  digitalWrite(GREEN_LED, HIGH);
}

void setWarningMode() {
  static unsigned long lastFlash = 0;
  if (millis() - lastFlash > 200) {
    digitalWrite(RED_LED, !digitalRead(RED_LED));
    digitalWrite(GREEN_LED, LOW);
    lastFlash = millis();
  }
}

void triggerViolation(unsigned long elapsed) {
  violationCount++;
  String plate = String(MOCK_PLATES[plateIndex % 6]);
  plateIndex++;

  JsonDocument doc;
  doc["event"] = "VIOLATION";
  doc["plate"] = plate;
  doc["lux"] = luxValue;
  doc["raw_adc"] = rawLdrValue;
  doc["vehicle_present"] = vehiclePresent;
  doc["violation_count"] = violationCount;
  doc["duration_ms"] = elapsed;
  
  publishMessage(TOPIC_VIOLATION, doc);

  // Hardware Alerts
  digitalWrite(RED_LED, HIGH);
  digitalWrite(GREEN_LED, LOW);
  tone(BUZZER_PIN, 1000, 500);
  
  // LCD Update
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("VIOLATION!");
  lcd.setCursor(0, 1);
  lcd.print(plate);
}

void sendSensorData(const char* state, unsigned long duration_ms) {
  if (millis() - lastSend > 2000) {
    JsonDocument doc;
    doc["event"] = "SENSOR";
    doc["lux"] = luxValue;
    doc["state"] = state;
    doc["vehicle_present"] = vehiclePresent;
    doc["duration_ms"] = duration_ms;

    publishMessage(TOPIC_SENSOR, doc);
    lastSend = millis();

    // LCD Update
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Lux: ");
    lcd.print(luxValue);
    lcd.setCursor(0, 1);
    lcd.print(state);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n\n--- WOKWI ESP32 BOOTING ---");
  
  pinMode(RED_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(VEHICLE_PIN, INPUT_PULLUP);

  Wire.begin(21, 22);
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.print("System Booting...");

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" Connected!");

  connectAWS();
  
  digitalWrite(GREEN_LED, HIGH); // Ready
}

void loop() {
  if (!client.connected() && millis() - lastAwsRetry > 10000) {
    lastAwsRetry = millis();
    connectAWS();
  }
  if (client.connected()) {
    client.loop();
  }

  rawLdrValue = analogRead(LDR_PIN);
  luxValue = adcToLux(rawLdrValue);
  vehiclePresent = (digitalRead(VEHICLE_PIN) == LOW);

  if (!vehiclePresent) {
    setNormalMode();
    violationSent = false;
    highBeamStart = 0;
    highBeamActive = false;
    sendSensorData("CLEAR", 0);
    delay(500);
    return;
  }

  if (luxValue > highBeamLux) {
    if (!highBeamActive) {
      highBeamActive = true;
      highBeamStart = millis();
    }
    unsigned long elapsed = millis() - highBeamStart;
    if (elapsed >= TRIGGER_DURATION && !violationSent) {
      triggerViolation(elapsed);
      violationSent = true;
    } else {
      setWarningMode();
      sendSensorData("WARNING", elapsed);
    }
  } else {
    highBeamActive = false;
    violationSent = false;
    highBeamStart = 0;
    if (luxValue > normalBeamLux) {
      setWarningMode();
      sendSensorData("NORMAL_BEAM", 0);
    } else {
      setNormalMode();
      sendSensorData("CLEAR", 0);
    }
  }
  delay(200);
}
