"""
aws_iot.py — AWS IoT Core MQTT bridge for ESP32 (Wokwi simulation)

FIXES:
  1. update_thing_shadow used wrong client ("iot-data" generic endpoint).
     Must use endpoint_url=https://{IOT_ENDPOINT} or it silently fails.
  2. publish_sensor_data / publish_violation now gracefully skip when
     IoT is not configured instead of crashing.
  3. Added service_status() method used by /api/service-status.
"""

import json, time, os, sys, ssl, threading
sys.path.insert(0, os.path.dirname(__file__))
from aws_config import get_client, aws_available, IOT_ENDPOINT
from aws_config import IOT_TOPIC_SUBSCRIBE, IOT_TOPIC_PUBLISH

CERT_DIR = os.path.join(os.path.dirname(__file__), "iot_certs")
ROOT_CA = os.path.join(CERT_DIR, "AmazonRootCA1.pem")
DEVICE_CERT = os.path.join(CERT_DIR, "certificate.pem.crt")
PRIVATE_KEY = os.path.join(CERT_DIR, "private.pem.key")


def _iot_data_client():
    """
    FIX: IoT Data client MUST include endpoint_url pointing to the
    account-specific ATS endpoint — otherwise boto3 defaults to the
    wrong regional endpoint and all publish/shadow calls silently fail.
    """
    if not aws_available() or not IOT_ENDPOINT:
        return None
    try:
        import boto3
        from aws_config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY
        return boto3.client(
            "iot-data",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            endpoint_url=f"https://{IOT_ENDPOINT}",   # FIX: required!
        )
    except Exception as e:
        print(f"[IoT] Client init failed: {e}")
        return None


class IoTBridge:
    """
    Publishes sensor data to AWS IoT Core.
    In Wokwi mode: Python acts as the 'ESP32' and publishes directly.
    In hardware mode: ESP32 publishes natively via AWS IoT SDK.
    """

    def __init__(self, on_message_callback=None):
        self.callback  = on_message_callback
        self._client   = None          # lazy-init
        self._mqtt_client = None
        self._mqtt_thread = None
        self._subscriber_running = False
        self.subscriber_connected = False
        self.last_message = None
        self.connected = False
        self._check_connection()

    def _check_connection(self):
        if not aws_available():
            print("[IoT] No AWS credentials — IoT Core disabled (local only)")
            return
        if not IOT_ENDPOINT:
            print("[IoT] IOT_ENDPOINT not set — IoT Core disabled")
            print("[IoT]   Run: python backend/aws_setup.py  to get your endpoint")
            return
        # Try creating client to verify reachability
        c = _iot_data_client()
        if c:
            self.connected = True
            print(f"[IoT] IoT Core configured: {IOT_ENDPOINT}")
        else:
            print("[IoT] IoT Core client init failed — check credentials")

    @property
    def client(self):
        """Lazy client: only created when actually needed."""
        if self._client is None and self.connected:
            self._client = _iot_data_client()
        return self._client

    def publish_sensor_data(self, data: dict):
        """Publish sensor reading to IoT Core topic."""
        if not self.client:
            return
        try:
            self.client.publish(
                topic=IOT_TOPIC_SUBSCRIBE,
                qos=1,
                payload=json.dumps(data)
            )
        except Exception as e:
            print(f"[IoT] Publish failed: {e}")
            self._client = None   # reset on error

    def publish_violation(self, violation: dict):
        """Publish confirmed violation to IoT violations topic."""
        if not self.client:
            return
        try:
            self.client.publish(
                topic=IOT_TOPIC_PUBLISH,
                qos=1,
                payload=json.dumps(violation)
            )
            print("[IoT] Violation published to cloud topic")
        except Exception as e:
            print(f"[IoT] Violation publish failed: {e}")
            self._client = None

    def get_shadow(self, thing_name: str = "HighBeamESP32") -> dict:
        """Get device shadow (current state) from IoT Core."""
        if not self.client:
            return {"state": {"reported": {"status": "simulated", "lux": 0}}}
        try:
            resp = self.client.get_thing_shadow(thingName=thing_name)
            return json.loads(resp["payload"].read())
        except Exception:
            return {"state": {"reported": {"status": "simulated", "lux": 0}}}

    def update_shadow(self, lux: int, status: str, violations_today: int):
        """Update device shadow with latest sensor state."""
        if not self.client:
            return
        try:
            shadow = json.dumps({
                "state": {
                    "reported": {
                        "lux":              lux,
                        "status":           status,
                        "violations_today": violations_today,
                        "last_updated":     time.time()
                    }
                }
            })
            # FIX: update_thing_shadow is on the iot-data client (with endpoint_url)
            self.client.update_thing_shadow(
                thingName="HighBeamESP32",
                payload=shadow
            )
        except Exception as e:
            print(f"[IoT] Shadow update failed: {e}")
            self._client = None

    def service_status(self) -> dict:
        """Return IoT Core health for /api/service-status."""
        return {
            "configured": bool(IOT_ENDPOINT),
            "connected":  self.connected,
            "subscriber_connected": self.subscriber_connected,
            "endpoint":   IOT_ENDPOINT or "not set",
            "topics": [IOT_TOPIC_SUBSCRIBE, IOT_TOPIC_PUBLISH],
        }

    def start_subscriber(self):
        """
        Subscribe to AWS IoT Core MQTT topics so Wokwi/ESP32 events update
        the Python backend and dashboard. Boto3 can publish IoT data, but it
        cannot keep an MQTT subscription open; paho-mqtt handles that path.
        """
        if self._subscriber_running:
            return True
        if not self.connected:
            print("[IoT] MQTT subscriber not started - IoT data client unavailable")
            return False
        missing = [p for p in (ROOT_CA, DEVICE_CERT, PRIVATE_KEY) if not os.path.exists(p)]
        if missing:
            print(f"[IoT] MQTT subscriber not started - missing cert files: {missing}")
            return False
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("[IoT] paho-mqtt not installed - run: pip install -r requirements.txt")
            return False

        self._subscriber_running = True
        client_id = f"HighBeamBackend-{os.getpid()}-{int(time.time())}"
        client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        client.tls_set(
            ca_certs=ROOT_CA,
            certfile=DEVICE_CERT,
            keyfile=PRIVATE_KEY,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.on_connect = self._on_mqtt_connect
        client.on_disconnect = self._on_mqtt_disconnect
        client.on_message = self._on_mqtt_message
        self._mqtt_client = client

        def _run():
            try:
                print(f"[IoT] MQTT subscriber connecting to {IOT_ENDPOINT}:8883 ...")
                client.connect(IOT_ENDPOINT, 8883, keepalive=60)
                client.loop_forever()
            except Exception as e:
                print(f"[IoT] MQTT subscriber stopped: {e}")
                self.subscriber_connected = False
                self._subscriber_running = False

        self._mqtt_thread = threading.Thread(target=_run, daemon=True)
        self._mqtt_thread.start()
        return True

    def stop_subscriber(self):
        self._subscriber_running = False
        self.subscriber_connected = False
        try:
            if self._mqtt_client:
                self._mqtt_client.disconnect()
        except Exception:
            pass

    def _on_mqtt_connect(self, client, userdata, flags, rc, *extra):
        try:
            code = int(rc.value) if hasattr(rc, "value") else int(rc)
        except Exception:
            code = 0 if str(rc).lower() == "success" else 1
        if code == 0:
            self.subscriber_connected = True
            client.subscribe([(IOT_TOPIC_SUBSCRIBE, 1), (IOT_TOPIC_PUBLISH, 1)])
            print(f"[IoT] MQTT subscriber connected. Listening: {IOT_TOPIC_SUBSCRIBE}, {IOT_TOPIC_PUBLISH}")
        else:
            self.subscriber_connected = False
            print(f"[IoT] MQTT subscriber connect failed: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc, *extra):
        self.subscriber_connected = False
        if self._subscriber_running:
            print(f"[IoT] MQTT subscriber disconnected: {rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            data = json.loads(payload)
            if msg.topic == IOT_TOPIC_PUBLISH:
                data["event"] = "VIOLATION"
            else:
                data.setdefault("event", "SENSOR")
            data["_source"] = "AWS_IOT"
            data["_topic"] = msg.topic
            self.last_message = data
            print(f"[IoT] MQTT <- {msg.topic}: lux={data.get('lux')} vehicle={data.get('vehicle_present')} event={data.get('event')}")
            if self.callback:
                self.callback(data)
        except Exception as e:
            print(f"[IoT] MQTT message ignored: {e}")


# ── ESP32 Arduino additions for IoT Core (reference only) ────────
ESP32_IOT_SNIPPET = """
// Add to sketch.ino for real AWS IoT Core connection
// Requires: AWS IoT Device SDK for Arduino (ESP32)

#include <WiFiClientSecure.h>
#include <MQTTClient.h>
#include <ArduinoJson.h>

const char* AWS_IOT_ENDPOINT = "YOUR_ENDPOINT.iot.ap-south-1.amazonaws.com";
const char* THING_NAME       = "HighBeamESP32";
const char* TOPIC_PUBLISH    = "highbeam/sensor/data";

WiFiClientSecure net;
MQTTClient client(1024);

void connectAWS() {
  net.setCACert(AWS_CERT_CA);
  net.setCertificate(AWS_CERT_CRT);
  net.setPrivateKey(AWS_CERT_PRIVATE);
  client.begin(AWS_IOT_ENDPOINT, 8883, net);
  while (!client.connect(THING_NAME)) delay(500);
}

void publishToCloud(int lux, bool vehicle, String plate) {
  StaticJsonDocument<200> doc;
  doc["lux"]    = lux;
  doc["vehicle"]= vehicle;
  doc["plate"]  = plate;
  doc["event"]  = "VIOLATION";
  char buf[200];
  serializeJson(doc, buf);
  client.publish(TOPIC_PUBLISH, buf);
}
"""
