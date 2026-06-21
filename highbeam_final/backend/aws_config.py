"""
aws_config.py — AWS client factory with free-tier service setup
All services fall within AWS free tier limits.
"""

import os
import boto3
from botocore.exceptions import NoCredentialsError, ClientError

# ── Load .env if present ─────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

AWS_REGION          = os.getenv("AWS_REGION",           "ap-south-1")   # Mumbai — closest to India
AWS_ACCESS_KEY      = os.getenv("AWS_ACCESS_KEY_ID",    "")
AWS_SECRET_KEY      = os.getenv("AWS_SECRET_ACCESS_KEY","")

# DynamoDB table names
DYNAMO_TABLE        = "highbeam_violations"
DYNAMO_PAYMENTS     = "highbeam_payments"

# S3 bucket name (must be globally unique — change to your name)
S3_BUCKET           = os.getenv("S3_BUCKET", "highbeam-detection-tn")

# SNS topic ARN (created by aws_setup.py)
SNS_TOPIC_ARN       = os.getenv("SNS_TOPIC_ARN", "")

# IoT Core endpoint (from AWS console → IoT Core → Settings)
IOT_ENDPOINT        = os.getenv("IOT_ENDPOINT", "")
IOT_TOPIC_SUBSCRIBE = "highbeam/sensor/data"
IOT_TOPIC_PUBLISH   = "highbeam/violations"

# ── Check if AWS creds are configured ────────────────────────────
def aws_available() -> bool:
    return bool(AWS_ACCESS_KEY and AWS_SECRET_KEY)

# ── Client factory ─────────────────────────────────────────────
def get_client(service: str):
    """Return boto3 client or None if credentials missing."""
    if not aws_available():
        return None
    try:
        return boto3.client(
            service,
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY
        )
    except Exception as e:
        print(f"[AWS] Failed to create {service} client: {e}")
        return None

def get_resource(service: str):
    """Return boto3 resource or None if credentials missing."""
    if not aws_available():
        return None
    try:
        return boto3.resource(
            service,
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY
        )
    except Exception as e:
        print(f"[AWS] Failed to create {service} resource: {e}")
        return None

def print_aws_status():
    if aws_available():
        print(f"[AWS] Credentials found — region: {AWS_REGION}")
        print(f"[AWS] DynamoDB table : {DYNAMO_TABLE}")
        print(f"[AWS] S3 bucket      : {S3_BUCKET}")
        print(f"[AWS] SNS topic      : {SNS_TOPIC_ARN or 'not set'}")
        print(f"[AWS] IoT endpoint   : {IOT_ENDPOINT or 'not set'}")
    else:
        print("[AWS] No credentials — running in local simulation mode")
        print("[AWS] Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env to enable cloud")
