"""
aws_database.py — DynamoDB (cloud) with SQLite (local) fallback
Automatically uses DynamoDB when AWS creds present, SQLite otherwise.
"""

import sqlite3, os, uuid, json
from datetime import datetime
from decimal import Decimal
import sys
sys.path.insert(0, os.path.dirname(__file__))
from aws_config import get_resource, aws_available, DYNAMO_TABLE, DYNAMO_PAYMENTS

FINE_FIRST   = 500
FINE_REPEAT  = 1000
DB_PATH      = os.path.join(os.path.dirname(__file__), "violations_local.db")

# ── Helpers ───────────────────────────────────────────────────────
def mask_plate(plate: str) -> str:
    """PDPB 2023 compliance: TN01AB1234 → TN01**1234"""
    if len(plate) >= 6:
        return plate[:4] + "**" + plate[6:]
    return plate[:2] + "**"

def float_to_decimal(obj):
    """DynamoDB requires Decimal not float."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 4)))
    return obj

# ── SQLite fallback ───────────────────────────────────────────────
def _sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_sqlite():
    conn = _sqlite_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS violations (
            id TEXT PRIMARY KEY, plate TEXT, plate_masked TEXT,
            lux INTEGER, confidence REAL, fine_amount INTEGER,
            offence_count INTEGER DEFAULT 1, timestamp TEXT,
            location TEXT DEFAULT 'Chennai - Anna Salai',
            status TEXT DEFAULT 'PENDING', alert_sent INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payments (
            violation_id TEXT, amount INTEGER, paid_at TEXT, method TEXT
        );
    """)
    conn.commit()
    conn.close()

# ── Main API ──────────────────────────────────────────────────────
def init_db():
    _init_sqlite()
    if aws_available():
        print("[DB] Using AWS DynamoDB (free tier)")
    else:
        print("[DB] Using local SQLite (no AWS creds)")

def _get_offence_count_dynamo(plate: str) -> int:
    try:
        db = get_resource("dynamodb")
        table = db.Table(DYNAMO_TABLE)
        resp = table.query(
            IndexName="plate-index",
            KeyConditionExpression="plate = :p",
            ExpressionAttributeValues={":p": plate}
        )
        return len(resp.get("Items", []))
    except Exception:
        return 0

def _get_offence_count_sqlite(plate: str) -> int:
    conn = _sqlite_conn()
    row = conn.execute("SELECT COUNT(*) as c FROM violations WHERE plate=?", (plate,)).fetchone()
    conn.close()
    return row["c"] if row else 0

def log_violation(plate: str, lux: int, confidence: float, location: str = None) -> dict:
    vid      = str(uuid.uuid4())[:8].upper()
    ts       = datetime.now().isoformat()
    loc      = location or "Chennai - Anna Salai"
    masked   = mask_plate(plate)

    if aws_available():
        count      = _get_offence_count_dynamo(plate) + 1
        fine       = FINE_REPEAT if count > 1 else FINE_FIRST
        item = {
            "id":            vid,
            "timestamp":     ts,
            "plate":         plate,
            "plate_masked":  masked,
            "lux":           lux,
            "confidence":    float_to_decimal(confidence),
            "fine_amount":   fine,
            "offence_count": count,
            "location":      loc,
            "status":        "PENDING",
            "alert_sent":    False
        }
        try:
            db = get_resource("dynamodb")
            db.Table(DYNAMO_TABLE).put_item(Item=item)
            _push_cloudwatch_metric(plate, fine)
            print(f"[DB] Violation saved to DynamoDB: {vid}")
        except Exception as e:
            print(f"[DB] DynamoDB write failed: {e} — saving to SQLite")
            _log_sqlite(vid, plate, masked, lux, confidence, fine, count, ts, loc)
        item["id"]         = vid
        item["confidence"] = confidence
        item["fine_amount"]= fine
        return item

    else:
        _init_sqlite()
        count = _get_offence_count_sqlite(plate) + 1
        fine  = FINE_REPEAT if count > 1 else FINE_FIRST
        _log_sqlite(vid, plate, masked, lux, confidence, fine, count, ts, loc)
        return {
            "id": vid, "plate": plate, "plate_masked": masked,
            "lux": lux, "confidence": confidence, "fine_amount": fine,
            "offence_count": count, "timestamp": ts,
            "location": loc, "status": "PENDING"
        }

def _log_sqlite(vid, plate, masked, lux, confidence, fine, count, ts, loc):
    conn = _sqlite_conn()
    conn.execute("""
        INSERT INTO violations
        (id,plate,plate_masked,lux,confidence,fine_amount,offence_count,timestamp,location)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (vid, plate, masked, lux, confidence, fine, count, ts, loc))
    conn.commit()
    conn.close()

def _push_cloudwatch_metric(plate: str, fine: int):
    """Send custom metric to CloudWatch (free up to 10 metrics)."""
    try:
        import boto3
        from aws_config import get_client
        cw = get_client("cloudwatch")
        if cw:
            cw.put_metric_data(
                Namespace="HighBeamDetection",
                MetricData=[
                    {"MetricName": "ViolationCount", "Value": 1, "Unit": "Count"},
                    {"MetricName": "FineAmount",     "Value": fine, "Unit": "Count"},
                ]
            )
    except Exception:
        pass

def get_all_violations(limit: int = 50) -> list:
    if aws_available():
        try:
            db = get_resource("dynamodb")
            resp = db.Table(DYNAMO_TABLE).scan(Limit=limit)
            items = resp.get("Items", [])
            # Convert Decimals back to float
            for item in items:
                if "confidence" in item:
                    item["confidence"] = float(item["confidence"])
                if "fine_amount" in item:
                    item["fine_amount"] = int(item["fine_amount"])
                if "lux" in item:
                    item["lux"] = int(item["lux"])
            items.sort(key=lambda x: x.get("timestamp",""), reverse=True)
            return items[:limit]
        except Exception as e:
            print(f"[DB] DynamoDB scan failed: {e}")

    _init_sqlite()
    conn = _sqlite_conn()
    rows = conn.execute("SELECT * FROM violations ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats() -> dict:
    violations = get_all_violations(1000)
    today = datetime.now().date().isoformat()
    violations = [
        v for v in violations
        if str(v.get("timestamp", "")).startswith(today)
    ]
    total      = len(violations)
    pending    = sum(1 for v in violations if v.get("status") == "PENDING")
    paid       = sum(1 for v in violations if v.get("status") == "PAID")
    amount     = sum(v.get("fine_amount", 0) for v in violations if v.get("status") == "PAID")
    repeat     = sum(1 for v in violations if v.get("offence_count", 1) > 1)
    return {
        "total_violations":  total,
        "pending_fines":     pending,
        "fines_paid":        paid,
        "total_fine_amount": amount,
        "repeat_offenders":  repeat,
        "storage_backend":   "DynamoDB" if aws_available() else "SQLite"
    }

def mark_paid(violation_id: str, method: str = "UPI") -> bool:
    if aws_available():
        try:
            db = get_resource("dynamodb")
            # Scan to find item (in production use GSI on id)
            resp = db.Table(DYNAMO_TABLE).scan(
                FilterExpression="id = :id",
                ExpressionAttributeValues={":id": violation_id}
            )
            items = resp.get("Items", [])
            if items:
                item = items[0]
                db.Table(DYNAMO_TABLE).update_item(
                    Key={"id": item["id"], "timestamp": item["timestamp"]},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "PAID"}
                )
                db.Table(DYNAMO_PAYMENTS).put_item(Item={
                    "violation_id": violation_id,
                    "amount":       item.get("fine_amount", 500),
                    "paid_at":      datetime.now().isoformat(),
                    "method":       method
                })
                return True
        except Exception as e:
            print(f"[DB] DynamoDB update failed: {e}")

    _init_sqlite()
    conn = _sqlite_conn()
    conn.execute("UPDATE violations SET status='PAID' WHERE id=?", (violation_id,))
    conn.commit()
    conn.close()
    return True

def mark_alert_sent(violation_id: str):
    if aws_available():
        try:
            db = get_resource("dynamodb")
            resp = db.Table(DYNAMO_TABLE).scan(
                FilterExpression="id = :id",
                ExpressionAttributeValues={":id": violation_id}
            )
            items = resp.get("Items", [])
            if items:
                item = items[0]
                db.Table(DYNAMO_TABLE).update_item(
                    Key={"id": item["id"], "timestamp": item["timestamp"]},
                    UpdateExpression="SET alert_sent = :v",
                    ExpressionAttributeValues={":v": True}
                )
            return
        except Exception:
            pass
    _init_sqlite()
    conn = _sqlite_conn()
    conn.execute("UPDATE violations SET alert_sent=1 WHERE id=?", (violation_id,))
    conn.commit()
    conn.close()
