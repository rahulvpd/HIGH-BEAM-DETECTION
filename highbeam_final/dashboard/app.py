"""
app.py — Flask dashboard with AWS integration

FIXES:
  1. /api/threshold GET + POST endpoint — changes reflect in detector + both UIs
  2. /api/simulate now updates system.latest_sensor/result so 3D dashboard enters real-data mode
  3. /api/stats includes current threshold so both dashboards can display it
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime
from aws_database import init_db, get_all_violations, get_stats, mark_paid
from aws_storage  import export_violations_csv, get_storage_usage
from aws_config   import aws_available
from main         import system

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/classic")
def classic():
    return render_template("classic.html")

@app.route("/api/violations")
def api_violations():
    today = datetime.now().date().isoformat()
    rows = [
        v for v in get_all_violations(1000)
        if str(v.get("timestamp", "")).startswith(today)
    ]
    return jsonify(rows[:50])

@app.route("/api/stats")
def api_stats():
    stats   = get_stats()
    ai      = system.detector.stats()
    storage = get_storage_usage()
    sensor = system.latest_sensor or {}
    received_at = float(sensor.get("_received_at", 0) or 0)
    sensor_live = bool(sensor) and (time.time() - received_at) <= 12
    live_sensor = sensor if sensor_live else {}
    live_result = system.latest_result if sensor_live else {}
    return jsonify({
        **stats, **ai,
        "sensor":     live_sensor,
        "result":     live_result,
        "sensor_live": sensor_live,
        "aws_active": aws_available(),
        "storage":    storage,
        # FIX: always include current threshold so dashboards can sync
        "threshold":  system.detector.high_beam_threshold,
        "threshold_normal": system.detector.normal_beam_threshold,
    })

# ── FIX: Threshold GET/POST endpoint ──────────────────────────────
@app.route("/api/threshold", methods=["GET", "POST"])
def api_threshold():
    if request.method == "GET":
        return jsonify(system.get_threshold())

    data = request.get_json(force=True, silent=True) or {}
    high   = 740
    normal = data.get("normal_beam_threshold")

    try:
        sensor_config_pushed = system.set_threshold(high, int(normal) if normal else None)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    print(f"[DASHBOARD] Threshold updated via API -> HIGH={system.detector.high_beam_threshold}")
    return jsonify({
        "success": True,
        "sensor_config_pushed": sensor_config_pushed,
        **system.get_threshold()
    })

@app.route("/api/pay/<vid>", methods=["POST"])
def api_pay(vid):
    mark_paid(vid)
    return jsonify({"success": True})

@app.route("/api/export")
def api_export():
    violations = get_all_violations(1000)
    path = export_violations_csv(violations)
    if os.path.isfile(path):
        return send_file(path, as_attachment=True, download_name="violations_export.csv")
    return jsonify({"success": True, "path": path})

@app.route("/api/crew-report", methods=["GET", "POST"])
def api_crew_report():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "ready": True,
            "police_email": "rahulrockindian7@gmail.com",
            "user_email": "rahuljuneten2002@gmail.com",
            "message": "POST this endpoint to send the full CrewAI report to police and a separate user-only summary to the user."
        })
    try:
        from analytics_crew import generate_and_email_daily_report
        data = request.get_json(force=True, silent=True) or {}
        user_plates = data.get("user_plates")
        if isinstance(user_plates, str):
            user_plates = [p.strip() for p in user_plates.split(",") if p.strip()]
        result = generate_and_email_daily_report(
            data.get("police_email", "rahulrockindian7@gmail.com"),
            data.get("user_email", "rahuljuneten2002@gmail.com"),
            user_plates,
        )
        success = (
            result["police_email"].get("success", False)
            and result["user_copy"]["email"].get("success", False)
        )
        return jsonify({"success": success, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """
    FIX: Simulate now:
      1. Generates lux ABOVE the current threshold (not hardcoded)
      2. Updates system.latest_sensor + system.latest_result
         so the 3D dashboard enters real-data mode showing the correct lux
      3. Sends real alerts
    """
    from aws_database import log_violation
    from datetime     import datetime
    import random, threading

    hi    = system.detector.high_beam_threshold
    # FIX: lux is always above the current threshold
    lux   = random.randint(hi, int(hi * 1.4))
    conf  = round(random.uniform(0.88, 0.97), 3)
    plate = random.choice(["TN01AB1234", "TN02CD5678", "TN03EF9012", "MH04GH3456"])
    dur   = random.randint(2100, 3500)

    v = log_violation(plate, lux=lux, confidence=conf)

    # FIX: Update live state so dashboard enters real-data mode
    system.latest_sensor = {
        "lux": lux,
        "vehicle_present": True,
        "event": "VIOLATION",
        "duration_ms": dur,
        "plate": plate,
        "_source": "SIMULATE_BUTTON",
        "_received_at": time.time(),
    }
    system.source = "SIMULATE_BUTTON"
    system.latest_result = {
        "decision":   "VIOLATION",
        "lux":        lux,
        "confidence": conf,
        "reason":     "SIMULATED",
        "timestamp":  datetime.now().isoformat(),
        "avg_lux":    lux,
        "threshold":  hi,
    }

    threading.Thread(target=system._alert_bg, args=(v,), daemon=True).start()
    return jsonify({"success": True, "violation": v})


# ── Service Status endpoint (used by both dashboards) ─────────────
@app.route("/api/service-status")
def api_service_status():
    """
    Returns detailed health of every AWS service.
    Dashboard polls this to show green/amber/red per service.
    """
    import os
    from aws_config import aws_available, AWS_REGION, SNS_TOPIC_ARN, IOT_ENDPOINT, S3_BUCKET

    aws_ok      = aws_available()
    alert_phone = os.getenv("ALERT_PHONE_NUMBER", "").strip()
    ses_email   = os.getenv("SES_FROM_EMAIL", "").strip()
    alert_email = os.getenv("ALERT_EMAIL", ses_email).strip()

    services = {
        "dynamodb": {
            "active": aws_ok,
            "label":  "DynamoDB" if aws_ok else "SQLite",
            "detail": f"Table: highbeam_violations ({AWS_REGION})" if aws_ok else "Local SQLite fallback",
        },
        "sns": {
            "active":  aws_ok,
            "label":   "Active" if aws_ok else "Simulated",
            "detail":  (f"Direct SMS → {alert_phone}" if alert_phone
                        else ("Topic mode — no ALERT_PHONE_NUMBER" if SNS_TOPIC_ARN
                              else "Set ALERT_PHONE_NUMBER in .env")),
            "sms_configured": bool(alert_phone),
        },
        "ses": {
            "active": aws_ok,
            "label":  "Active" if aws_ok else "Simulated",
            "detail": f"From: {ses_email} → {alert_email}" if (aws_ok and ses_email) else "Set SES_FROM_EMAIL in .env",
            "email_configured": bool(ses_email),
        },
        "iot": {
            "active":  system.iot_bridge.connected or system.iot_bridge.subscriber_connected,
            "label":   "MQTT Listening" if system.iot_bridge.subscriber_connected
                       else ("Connected" if system.iot_bridge.connected else ("Not configured" if not IOT_ENDPOINT else "Error")),
            "detail":  (f"{IOT_ENDPOINT} | topics: highbeam/sensor/data, highbeam/violations"
                        if IOT_ENDPOINT else "Run aws_setup.py to get endpoint"),
        },
        "s3": {
            "active": aws_ok,
            "label":  "Active" if aws_ok else "Local",
            "detail": f"Bucket: {S3_BUCKET}" if aws_ok else "Reports saved locally",
        },
    }

    # Live sensor source
    sensor = system.latest_sensor or {}
    received_at = float(sensor.get("_received_at", 0) or 0)
    sensor_live = bool(sensor) and (time.time() - received_at) <= 12
    services["sensor"] = {
        "active": sensor_live,
        "label":  system.source if sensor_live and system.source != "NONE" else "Waiting",
        "detail": f"Source: {system.source}" if sensor_live else "No recent sensor packet",
    }

    return jsonify({
        "aws_available":  aws_ok,
        "region":         AWS_REGION,
        "threshold":      system.detector.high_beam_threshold,
        "services":       services,
        "alert_phone":    alert_phone or None,
        "ses_email":      ses_email or None,
    })

if __name__ == "__main__":
    init_db()
    system.start()
    print("[DASHBOARD] http://localhost:5000")
    app.run(debug=True, use_reloader=False, port=5000)

# ── FIX: SMS test endpoint for classic dashboard panel ────────────
@app.route("/api/test-sms", methods=["POST"])
def api_test_sms():
    """Send a test SMS to a user-specified phone number."""
    import os
    data  = request.get_json(force=True, silent=True) or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "error": "phone required"}), 400

    from aws_config import get_client, aws_available
    from datetime   import datetime

    msg = (
        f"HighBeam AI Test\n"
        f"System: ONLINE | Time: {datetime.now().strftime('%H:%M:%S')}\n"
        f"SNS SMS is working correctly."
    )

    if aws_available():
        sns = get_client("sns")
        if sns:
            try:
                resp = sns.publish(
                    PhoneNumber=phone,
                    Message=msg,
                    MessageAttributes={
                        "AWS.SNS.SMS.SMSType": {"DataType":"String","StringValue":"Transactional"}
                    }
                )
                return jsonify({"success": True, "message_id": resp["MessageId"], "to": phone})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)})

    # Simulated
    print(f"[SMS TEST SIMULATED] To: {phone}\n{msg}")
    return jsonify({"success": True, "simulated": True, "to": phone})
