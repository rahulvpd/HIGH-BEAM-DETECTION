"""
aws_storage.py — S3 report storage + CSV export (free tier: 5 GB)
"""

import os, sys, json, csv, io, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from aws_config import get_client, aws_available, S3_BUCKET

_usage_cache = {"expires": 0.0, "value": None}

def _s3():
    return get_client("s3")

def upload_violation_log(violations: list) -> str:
    """Upload full violation log as JSON to S3. Returns URL or local path."""
    content  = json.dumps(violations, indent=2, default=str)
    key      = f"logs/violations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    if aws_available():
        try:
            s3 = _s3()
            s3.put_object(
                Bucket=S3_BUCKET, Key=key,
                Body=content.encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="AES256"  # free encryption
            )
            url = f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
            print(f"[S3] Log uploaded: {url}")
            return url
        except Exception as e:
            print(f"[S3] Upload failed: {e}")

    # Local fallback
    local = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(local, exist_ok=True)
    path = os.path.join(local, os.path.basename(key))
    with open(path, "w") as f:
        f.write(content)
    print(f"[S3 LOCAL] Log saved: {path}")
    return path

def export_violations_csv(violations: list) -> str:
    """Export violations to CSV and upload to S3."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "id","plate_masked","lux","confidence","fine_amount",
        "offence_count","timestamp","location","status"
    ])
    writer.writeheader()
    for v in violations:
        writer.writerow({k: v.get(k,"") for k in writer.fieldnames})

    content = output.getvalue()
    key     = f"exports/violations_{datetime.now().strftime('%Y%m%d')}.csv"

    if aws_available():
        try:
            s3 = _s3()
            s3.put_object(
                Bucket=S3_BUCKET, Key=key,
                Body=content.encode("utf-8"),
                ContentType="text/csv",
                ServerSideEncryption="AES256"
            )
            print(f"[S3] CSV exported to s3://{S3_BUCKET}/{key}")
        except Exception as e:
            print(f"[S3] CSV export failed: {e}")

    # Always save local copy (for Flask send_file + backup)
    local = os.path.join(os.path.dirname(__file__), "reports", os.path.basename(key))
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "w") as f:
        f.write(content)
    print(f"[S3 LOCAL] CSV saved: {local}")
    return local

def get_storage_usage() -> dict:
    """Get S3 bucket size (for dashboard display)."""
    cached = _usage_cache.get("value")
    if cached and time.time() < _usage_cache.get("expires", 0):
        return cached

    if aws_available():
        try:
            from botocore.config import Config
            import boto3
            from aws_config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY
            cw = boto3.client(
                "cloudwatch",
                region_name=AWS_REGION,
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 1}),
            )
            resp = cw.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[
                    {"Name": "BucketName",  "Value": S3_BUCKET},
                    {"Name": "StorageType", "Value": "StandardStorage"}
                ],
                StartTime=datetime.utcnow().replace(hour=0, minute=0),
                EndTime=datetime.utcnow(),
                Period=86400,
                Statistics=["Average"]
            ) if cw else {"Datapoints": []}
            points = resp.get("Datapoints", [])
            size   = int(points[-1]["Average"]) if points else 0
            value = {"bytes": size, "mb": round(size/1024/1024, 2), "bucket": S3_BUCKET}
            _usage_cache.update({"expires": time.time() + 300, "value": value})
            return value
        except Exception:
            value = {"bytes": 0, "mb": 0.0, "bucket": S3_BUCKET}
            _usage_cache.update({"expires": time.time() + 60, "value": value})
            return value
    return {"bytes": 0, "mb": 0.0, "bucket": "local"}
