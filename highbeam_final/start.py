"""
start.py — One-click launcher for High Beam Detection System

FIXES:
  1. Removed fragile os.chdir() — Flask templates now resolve via absolute path
  2. Added service pre-flight check with clear pass/fail status
  3. Prints startup summary with all service states
  4. Graceful error messages if port 5000 is in use
"""

import subprocess, sys, os, time, webbrowser, threading

try:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
except AttributeError:
    pass

BASE     = os.path.dirname(os.path.abspath(__file__))
BACKEND  = os.path.join(BASE, "backend")
DASHBOARD= os.path.join(BASE, "dashboard")

# Ensure both dirs are on the import path before any import
sys.path.insert(0, BACKEND)
sys.path.insert(0, DASHBOARD)


def install_deps():
    print("[START] Checking dependencies...")
    req = os.path.join(BASE, "requirements.txt")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req, "-q"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[START] pip install warning:\n{result.stderr[:400]}")
    else:
        print("[START] Dependencies ready [OK]")


def preflight_check():
    """FIX: Print clear service status before starting."""
    print()
    print("  SERVICE STATUS")
    print("  " + "-"*40)

    # AWS
    try:
        from aws_config import aws_available, AWS_REGION
        aws_ok = aws_available()
        print(f"  {'✓' if aws_ok else '!'} AWS Credentials  {'Found (region: '+AWS_REGION+')' if aws_ok else 'NOT SET — running in simulation mode'}")
    except Exception as e:
        print(f"  ✗ AWS Config       {e}")
        aws_ok = False

    # SNS phone
    alert_phone = os.getenv("ALERT_PHONE_NUMBER", "").strip()
    if alert_phone:
        print(f"  ✓ SMS (SNS)        Direct to {alert_phone}")
    elif aws_ok:
        print(f"  ! SMS (SNS)        Topic mode — set ALERT_PHONE_NUMBER in .env for direct SMS")
    else:
        print(f"  - SMS (SNS)        Simulated (no AWS credentials)")

    # SES email
    ses_email = os.getenv("SES_FROM_EMAIL", "").strip()
    alert_email = os.getenv("ALERT_EMAIL", ses_email).strip()
    if alert_email and aws_ok:
        print(f"  ✓ Email (SES)      {alert_email}")
    else:
        print(f"  - Email (SES)      {'Simulated' if not aws_ok else 'ALERT_EMAIL not set'}")

    # IoT Core
    iot_ep = os.getenv("IOT_ENDPOINT", "").strip()
    if iot_ep and aws_ok:
        print(f"  ✓ IoT Core         {iot_ep[:45]}...")
    else:
        print(f"  - IoT Core         {'Not configured — run backend/aws_setup.py' if aws_ok else 'Disabled (no AWS)'}")

    # DB
    from aws_config import aws_available as _aw
    print(f"  ✓ Database         {'DynamoDB' if _aw() else 'SQLite (local fallback)'}")

    print("  " + "-"*40)
    print()


def open_browser():
    time.sleep(3.5)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    # Load .env first
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE, ".env"))
    except ImportError:
        pass

    print("=" * 55)
    print("  HIGH BEAM DETECTION — IoT + AI + AWS Cloud")
    print("=" * 55)

    install_deps()
    preflight_check()

    # FIX: Import after path setup (no chdir needed)
    from aws_database import init_db
    from app import app, system   # app.py uses __file__-relative paths — safe

    init_db()
    system.start()

    threading.Thread(target=open_browser, daemon=True).start()

    print("[START] Dashboard → http://localhost:5000")
    print("[START] Classic   → http://localhost:5000/classic")
    print("[START] Press Ctrl+C to stop\n")

    try:
        app.run(debug=False, use_reloader=False, port=5000, host="0.0.0.0")
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print("\n[START] ERROR: Port 5000 is already in use!")
            print("[START] Kill the existing process or change port:")
            print("[START]   Windows: netstat -ano | findstr :5000")
            print("[START]   Linux:   lsof -i :5000 | kill -9 <PID>")
        else:
            raise
