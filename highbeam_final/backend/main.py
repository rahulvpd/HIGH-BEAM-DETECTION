"""
main.py â€” High Beam Detection System â€” Main orchestrator
Integrates: AI detector -> AWS DynamoDB -> SNS/SES -> S3

FIXES:
  1. UDP completely removed. System assumes direct ESP32 to AWS IoT connection.
  2. set_threshold() method exposed for dashboard API
  3. Violation logging now works for SENSOR events too
"""

import json, time, threading, random, sys, os
sys.path.insert(0, os.path.dirname(__file__))

from detector      import HighBeamDetector
from aws_database  import init_db, log_violation, get_stats, mark_alert_sent
from aws_alerts    import send_violation_alerts
from aws_iot       import IoTBridge
from aws_config    import aws_available, print_aws_status

SIMULATE_MODE = False  # False = read from shadow/serial, True = fake data

# â”€â”€ Serial config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
WOKWI_SERIAL  = "rfc2217://127.0.0.1:4000"
SERIAL_PORT   = "COM3"
SERIAL_BAUD   = 115200
SIM_INTERVAL  = 2.0

PLATES = ["TN01AB1234","TN02CD5678","TN03EF9012","MH04GH3456","KA05IJ7890","AP06KL2345"]

class HighBeamSystem:
    def __init__(self):
        init_db()
        self.detector      = HighBeamDetector()
        self.iot_bridge    = IoTBridge(on_message_callback=self._process)
        self.running       = False
        self.latest_sensor = {}
        self.latest_result = {}
        self.thread        = None
        self.source        = "NONE"
        self._last_violation_ts: float = 0.0
        self._violation_cooldown = 5.0   # seconds between logging the same incident
        self._violation_active = False
        print_aws_status()
        print("[SYSTEM] High Beam Detection System initialized (TCP/MQTT only)")

    def set_threshold(self, high: int, normal=None):
        self.detector.set_threshold(high, normal)
        self.push_threshold_to_sensor()
        if self.latest_sensor:
            latest = dict(self.latest_sensor)
            latest["_source"] = latest.get("_source", self.source or "THRESHOLD_RECHECK")
            print("[SYSTEM] Rechecking latest sensor data after threshold update")
            self._process(latest)
        return True

    def get_threshold(self) -> dict:
        return {
            "high_beam_threshold":   self.detector.high_beam_threshold,
            "normal_beam_threshold": self.detector.normal_beam_threshold,
        }

    def push_threshold_to_sensor(self):
        """Send current dashboard thresholds back to the ESP32 via AWS IoT Shadow or Topic."""
        # Now relying on AWS IoT shadow or direct topic publish instead of UDP
        print("[SYSTEM] Configuration updated. ESP32 will pick this up via MQTT.")
        try:
            # Publish config to the MQTT topic the ESP32 is subscribed to
            if self.iot_bridge.client:
                self.iot_bridge.client.publish(
                    topic="highbeam/config",
                    qos=1,
                    payload=json.dumps(self.get_threshold())
                )
        except Exception as e:
            print(f"[SYSTEM] Failed to push config via MQTT: {e}")

    def _simulate(self):
        self.source = "SIMULATION"
        print("[SYSTEM] Running in SIMULATION mode (no sensor)")
        scenarios = [
            (120,  False, "SENSOR",    0,    "Road clear"),
            (820,  False, "SENSOR",    2500, "Streetlight FP test"),
            (280,  True,  "SENSOR",    500,  "Normal beam"),
            (760,  True,  "WARNING",   1600, "Building up..."),
            (840,  True,  "VIOLATION", 2100, "High beam!"),
            (900,  True,  "VIOLATION", 2800, "High beam!"),
            (150,  False, "CLEAR",     0,    "Clear"),
            (300,  True,  "SENSOR",    600,  "Normal beam"),
            (950,  True,  "VIOLATION", 3100, "High beam!"),
            (100,  False, "CLEAR",     0,    "Clear"),
        ]
        plate_idx = 0

    def get_threshold(self) -> dict:
        return {
            "high_beam_threshold":   self.detector.high_beam_threshold,
            "normal_beam_threshold": self.detector.normal_beam_threshold,
        }

    def push_threshold_to_sensor(self):
        """Send current dashboard thresholds back to the ESP32 via AWS IoT Shadow or Topic."""
        # Now relying on AWS IoT shadow or direct topic publish instead of UDP
        print("[SYSTEM] Configuration updated. ESP32 will pick this up via MQTT.")
        try:
            # Publish config to the MQTT topic the ESP32 is subscribed to
            if self.iot_bridge.client:
                self.iot_bridge.client.publish(
                    topic="highbeam/config",
                    qos=1,
                    payload=json.dumps(self.get_threshold())
                )
        except Exception as e:
            print(f"[SYSTEM] Failed to push config via MQTT: {e}")

    def _simulate(self):
        self.source = "SIMULATION"
        print("[SYSTEM] Running in SIMULATION mode (no sensor)")
        scenarios = [
            (120,  False, "SENSOR",    0,    "Road clear"),
            (820,  False, "SENSOR",    2500, "Streetlight FP test"),
            (280,  True,  "SENSOR",    500,  "Normal beam"),
            (760,  True,  "WARNING",   1600, "Building up..."),
            (840,  True,  "VIOLATION", 2100, "High beam!"),
            (900,  True,  "VIOLATION", 2800, "High beam!"),
            (150,  False, "CLEAR",     0,    "Clear"),
            (300,  True,  "SENSOR",    600,  "Normal beam"),
            (950,  True,  "VIOLATION", 3100, "High beam!"),
            (100,  False, "CLEAR",     0,    "Clear"),
        ]
        plate_idx = 0
        idx       = 0
        while self.running:
            lux, vehicle, event, dur, _ = scenarios[idx % len(scenarios)]
            lux = max(0, lux + random.randint(-20, 20))
            data = {"lux": lux, "vehicle_present": vehicle,
                    "event": event, "duration_ms": dur}
            if event == "VIOLATION":
                data["plate"] = PLATES[plate_idx % len(PLATES)]
                plate_idx += 1
            self._process(data)
            self.iot_bridge.publish_sensor_data(data)
            idx += 1
            time.sleep(SIM_INTERVAL)

    def _read_wokwi(self):
        """Read serial data from Wokwi VS Code extension (RFC 2217)."""
        try:
            import serial
            from serial import serial_for_url
            ser = serial_for_url(WOKWI_SERIAL, baudrate=SERIAL_BAUD, timeout=1)
            self.source = "WOKWI_SENSOR"
            last_data_time = time.time()
            while self.running:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    if time.time() - last_data_time > 15:
                        break
                    continue
                last_data_time = time.time()
                try:
                    data = self._parse_sensor_line(line)
                    if not data:
                        continue
                    if data.get("status") == "READY":
                        continue
                    data["_source"] = "WOKWI_TCP"
                    self._process(data)
                except Exception:
                    pass
        except Exception:
            return False
        return True

    def _read_serial(self):
        """Read from physical COM port (USB ESP32)."""
        try:
            import serial
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
            self.source = "USB_SENSOR"
            print(f"[SERIAL] Connected: {SERIAL_PORT}")
            last_data_time = time.time()
            while self.running:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    if time.time() - last_data_time > 10:
                        print("[SERIAL] Disconnecting due to 10s idle timeout (no physical packets)")
                        break
                    continue
                last_data_time = time.time()
                try:
                    data = self._parse_sensor_line(line)
                    if not data:
                        continue
                    if data.get("status") == "READY":
                        print(f"[SERIAL] ESP32 device ready: {data.get('device','')}")
                        continue
                    data["_source"] = "USB_SERIAL"
                    print(f"[SERIAL] <- {data}")
                    self._process(data)
                except Exception:
                    pass
        except Exception as e:
            print(f"[SERIAL] Error: {e}")
            return False
        return True

    def _parse_sensor_line(self, line: str):
        """Accept pure JSON or logs that contain one JSON object after a prefix."""
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            start = line.find("{")
            end = line.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                return json.loads(line[start:end + 1])
            except json.JSONDecodeError:
                return None

    def _auto_connect(self):
        """Try Wokwi Serial -> physical serial -> retry loop."""
        print("[SYSTEM] Auto-detecting sensor source ...")
        while self.running:
            if self._read_wokwi():
                continue
            print("[SYSTEM] Trying physical serial port ...")
            if self._read_serial():
                continue
            print("[SYSTEM] No local serial sensor found. Relying on AWS IoT Core MQTT... ")
            # Note: For full MQTT Python subscribe, paho-mqtt or awsiotsdk is needed.
            # Currently backend fetches via shadow or acts purely as dashboard API backend.
            time.sleep(10)

    def _process(self, data: dict):
        data["_received_at"] = time.time()
        self.latest_sensor = data
        source = data.get("_source")
        if source:
            self.source = source
        result = self.detector.analyze(data)
        self.latest_result = result

        decision = result["decision"]
        event    = data.get("event", "SENSOR")

        is_violation = (
            decision == "VIOLATION" and
            (event == "VIOLATION" or (event == "SENSOR" and result.get("confidence", 0) >= 0.85))
        )

        if not data.get("vehicle_present", False) or result["lux"] <= self.detector.high_beam_threshold:
            self._violation_active = False

        if is_violation:
            now = time.time()
            if not self._violation_active and now - self._last_violation_ts > self._violation_cooldown:
                self._violation_active = True
                self._last_violation_ts = now
                plate     = data.get("plate", PLATES[0])
                violation = log_violation(plate, result["lux"], result["confidence"])
                print(f"[VIOLATION] {violation['plate_masked']} | "
                      f"Rs.{violation['fine_amount']} | #{violation['offence_count']}")
                self.iot_bridge.update_shadow(result["lux"], "VIOLATION", 1)
                threading.Thread(target=self._alert_bg, args=(violation,), daemon=True).start()

    def _alert_bg(self, violation):
        r = send_violation_alerts(violation)
        mark_alert_sent(violation["id"])
        sms_ok   = r['sms']['success']
        email_ok = r['email']['success']
        sim_sms  = r['sms'].get('simulated', False)
        sim_email = r['email'].get('simulated', False)
        print(f"[ALERT] SMS={'OK' if sms_ok else 'FAIL'}{'(sim)' if sim_sms else ''} | "
              f"Email={'OK' if email_ok else 'FAIL'}{'(sim)' if sim_email else ''}")

    def start(self):
        self.running = True
        self.iot_bridge.start_subscriber()
        target = self._simulate if SIMULATE_MODE else self._auto_connect
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        print("[SYSTEM] Detection running")

    def stop(self):
        self.running = False
        self.iot_bridge.stop_subscriber()

system = HighBeamSystem()

if __name__ == "__main__":
    system.start()
    try:
        while True:
            s = get_stats()
            a = system.detector.stats()
            print(f"[STATS] violations={s['total_violations']} | "
                  f"backend={s['storage_backend']} | AI={a['accuracy']}%")
            time.sleep(10)
    except KeyboardInterrupt:
        system.stop()
