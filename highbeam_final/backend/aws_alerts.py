"""
aws_alerts.py — SNS (SMS) + SES (Email) alerts

FIXES:
  1. ALERT_PHONE_NUMBER env var: set a real number to receive demo SMS
  2. When SNS_TOPIC_ARN set but no subscribers, falls back to direct SMS
     to ALERT_PHONE_NUMBER (if configured)
  3. Clearer simulation logs so you can tell what's happening
"""

import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from aws_config import get_client, aws_available, SNS_TOPIC_ARN

# ── Owner registry (mock data for demo) ──────────────────────────
MOCK_OWNERS = {
    "TN01AB1234": {"name": "Ravi Kumar",   "phone": "+919876543210", "email": "ravi@example.com"},
    "TN02CD5678": {"name": "Priya Singh",  "phone": "+919876543211", "email": "priya@example.com"},
    "TN03EF9012": {"name": "Arjun Das",    "phone": "+919876543212", "email": "arjun@example.com"},
    "MH04GH3456": {"name": "Sunita Patil", "phone": "+919876543213", "email": "sunita@example.com"},
    "KA05IJ7890": {"name": "Vikram Rao",   "phone": "+919876543214", "email": "vikram@example.com"},
    "AP06KL2345": {"name": "Lakshmi Devi", "phone": "+919876543215", "email": "lakshmi@example.com"},
}
DEFAULT_OWNER = {"name": "Vehicle Owner", "phone": "+919000000000", "email": "owner@example.com"}

# FIX: Set ALERT_PHONE_NUMBER in .env to receive real SMS on your phone
#      Format: +91XXXXXXXXXX  (include country code)
ALERT_PHONE_NUMBER = os.getenv("ALERT_PHONE_NUMBER", "").strip()
ALERT_EMAIL        = os.getenv("ALERT_EMAIL", os.getenv("SES_FROM_EMAIL", "")).strip()
SES_FROM           = os.getenv("SES_FROM_EMAIL", "noreply@yourdomain.com")

def get_owner(plate: str) -> dict:
    return MOCK_OWNERS.get(plate, DEFAULT_OWNER)

# ── SMS via AWS SNS ───────────────────────────────────────────────
def send_sms(violation: dict) -> dict:
    owner = get_owner(violation.get("plate", ""))
    msg = (
        f"TRAFFIC VIOLATION - Tamil Nadu Police\n"
        f"Vehicle: {violation['plate_masked']}\n"
        f"Offence: High beam headlight\n"
        f"Fine: Rs.{violation['fine_amount']}\n"
        f"Ref: {violation['id']}\n"
        f"Pay: echallan.parivahan.gov.in"
    )

    if aws_available():
        sns = get_client("sns")
        if sns:
            # FIX: Priority order for SMS delivery:
            # 1. ALERT_PHONE_NUMBER (real number from .env) → guaranteed delivery
            # 2. SNS topic (only works if someone subscribed a phone to it)
            # 3. Mock owner phone (for demo only, not a real number)
            target_phone = ALERT_PHONE_NUMBER or (None if SNS_TOPIC_ARN else owner["phone"])

            if target_phone:
                try:
                    resp = sns.publish(
                        PhoneNumber=target_phone,
                        Message=msg,
                        MessageAttributes={
                            "AWS.SNS.SMS.SMSType": {
                                "DataType": "String",
                                "StringValue": "Transactional"
                            },
                            "AWS.SNS.SMS.SenderID": {
                                "DataType": "String",
                                "StringValue": "TNPOLICE"
                            }
                        }
                    )
                    print(f"[SNS] SMS sent to {target_phone} — MessageId: {resp['MessageId']}")
                    return {"success": True, "message_id": resp["MessageId"], "to": target_phone}
                except Exception as e:
                    print(f"[SNS] Direct SMS failed: {e}")

            if SNS_TOPIC_ARN:
                try:
                    resp = sns.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Message=msg,
                        Subject="Traffic Violation Notice"
                    )
                    print(f"[SNS] Published to topic — MessageId: {resp['MessageId']}")
                    if not ALERT_PHONE_NUMBER:
                        print("[SNS] WARNING: No ALERT_PHONE_NUMBER set and no direct phone.")
                        print("[SNS]   -> Topic published but only reaches subscribed phones.")
                        print("[SNS]   -> Add ALERT_PHONE_NUMBER=+91XXXXXXXXXX to .env for real SMS.")
                    return {"success": True, "message_id": resp["MessageId"], "to": "topic"}
                except Exception as e:
                    print(f"[SNS] Topic publish failed: {e}")

    # Simulation mode
    delivery_note = ""
    if not ALERT_PHONE_NUMBER:
        delivery_note = " [Set ALERT_PHONE_NUMBER in .env for real SMS]"
    print(f"[SNS SIMULATED]{delivery_note}")
    print(f"  To: {owner['phone']}")
    print(f"  Message:\n{msg}")
    return {"success": True, "simulated": True, "to": owner["phone"]}

# ── Email via AWS SES ─────────────────────────────────────────────
def send_email(violation: dict) -> dict:
    owner    = get_owner(violation.get("plate", ""))
    # FIX: if ALERT_EMAIL is set, send there too (useful for demos)
    to_email = ALERT_EMAIL if ALERT_EMAIL and ALERT_EMAIL != SES_FROM else owner["email"]
    ref_id   = violation["id"]
    fine     = violation["fine_amount"]
    count    = violation.get("offence_count", 1)

    html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:580px;margin:auto;color:#333;">
  <div style="background:#c62828;padding:18px 24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;font-size:18px;">Traffic Violation Notice</h2>
    <p style="color:#ffcdd2;margin:4px 0 0;font-size:13px;">Tamil Nadu Traffic Police — IoT Detection System</p>
  </div>
  <div style="background:#fafafa;padding:20px 24px;border:1px solid #e0e0e0;border-top:none;">
    <p>Dear <b>{owner['name']}</b>,</p>
    <p>Your vehicle was detected using <b>high beam headlights</b> in violation of Rule 106, CMV Rules.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px;">
      <tr style="background:#fff;"><td style="padding:9px 12px;color:#666;border:1px solid #e0e0e0;">Vehicle</td>
        <td style="padding:9px 12px;font-weight:bold;border:1px solid #e0e0e0;">{violation['plate_masked']}</td></tr>
      <tr style="background:#f5f5f5;"><td style="padding:9px 12px;color:#666;border:1px solid #e0e0e0;">Date/Time</td>
        <td style="padding:9px 12px;border:1px solid #e0e0e0;">{violation['timestamp'][:19].replace('T',' ')}</td></tr>
      <tr style="background:#fff;"><td style="padding:9px 12px;color:#666;border:1px solid #e0e0e0;">Location</td>
        <td style="padding:9px 12px;border:1px solid #e0e0e0;">{violation.get('location','Chennai - Anna Salai')}</td></tr>
      <tr style="background:#f5f5f5;"><td style="padding:9px 12px;color:#666;border:1px solid #e0e0e0;">Offence No.</td>
        <td style="padding:9px 12px;border:1px solid #e0e0e0;">{'1st' if count==1 else str(count)+'th'} offence</td></tr>
      <tr style="background:#c62828;"><td style="padding:11px 12px;color:#fff;font-weight:bold;border:1px solid #b71c1c;">Fine Amount</td>
        <td style="padding:11px 12px;color:#fff;font-weight:bold;font-size:17px;border:1px solid #b71c1c;">&#8377;{fine}</td></tr>
    </table>
    <div style="background:#fff8e1;border-left:3px solid #f9a825;padding:10px 14px;margin:12px 0;border-radius:0 4px 4px 0;">
      <b>Reference:</b> {ref_id} &nbsp;|&nbsp; <b>Pay at:</b> echallan.parivahan.gov.in
    </div>
    <p style="color:#888;font-size:12px;">Section 177, Motor Vehicles Act 1988 | PDPB 2023 compliant | AWS Cloud System</p>
  </div>
</body></html>"""

    if aws_available():
        try:
            ses = get_client("ses")
            if ses:
                resp = ses.send_email(
                    Source=SES_FROM,
                    Destination={"ToAddresses": [to_email]},
                    Message={
                        "Subject": {"Data": f"Violation Notice {ref_id} — Fine Rs.{fine}"},
                        "Body": {
                            "Html": {"Data": html},
                            "Text": {"Data": f"Violation {ref_id}. Fine: Rs.{fine}. Pay at echallan.parivahan.gov.in"}
                        }
                    }
                )
                print(f"[SES] Email sent to {to_email} — MessageId: {resp['MessageId']}")
                return {"success": True, "message_id": resp["MessageId"], "to": to_email}
        except Exception as e:
            print(f"[SES] Failed: {e} — simulating")

    print(f"[SES SIMULATED] Email to: {to_email} | Ref: {ref_id} | Fine: Rs.{fine}")
    return {"success": True, "simulated": True, "to": to_email}

def send_violation_alerts(violation: dict) -> dict:
    return {
        "sms":   send_sms(violation),
        "email": send_email(violation)
    }
