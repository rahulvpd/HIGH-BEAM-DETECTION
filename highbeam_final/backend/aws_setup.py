"""
aws_setup.py — One-time AWS resource provisioner
Run once to create all required AWS free-tier resources.

FIXES:
  1. Auto-writes SNS_TOPIC_ARN back to .env (no more manual copy-paste)
  2. Subscribes ALERT_PHONE_NUMBER to the SNS topic automatically
     so direct SMS actually works without a separate step
  3. Subscribes ALERT_EMAIL to the SNS topic for email delivery
  4. Verifies SES sender email identity if not already verified
  5. IoT endpoint written to .env automatically

Usage: python backend/aws_setup.py
"""

import sys, os, json, time, re
sys.path.insert(0, os.path.dirname(__file__))
from aws_config import (get_client, get_resource, aws_available,
                         DYNAMO_TABLE, DYNAMO_PAYMENTS, S3_BUCKET,
                         AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY)

ENV_PATH = os.path.join(os.path.dirname(__file__), '..', '.env')


def _update_env(key: str, value: str):
    """Write or update a key=value line in .env."""
    with open(ENV_PATH, 'r') as f:
        lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        if line.startswith(key + '=') or line.startswith(key + ' ='):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(ENV_PATH, 'w') as f:
        f.writelines(lines)
    print(f"[SETUP] .env updated: {key}={value}")


def create_dynamodb_tables():
    dynamodb = get_resource("dynamodb")
    if not dynamodb:
        print("[SETUP] Skipping DynamoDB — no AWS credentials"); return

    existing = [t.name for t in dynamodb.tables.all()]

    if DYNAMO_TABLE not in existing:
        table = dynamodb.create_table(
            TableName=DYNAMO_TABLE,
            KeySchema=[
                {"AttributeName": "id",        "KeyType": "HASH"},
                {"AttributeName": "timestamp",  "KeyType": "RANGE"}
            ],
            AttributeDefinitions=[
                {"AttributeName": "id",        "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
                {"AttributeName": "plate",     "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "plate-index",
                "KeySchema": [{"AttributeName": "plate", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}
            }],
            BillingMode="PAY_PER_REQUEST"
        )
        table.wait_until_exists()
        print(f"[SETUP] DynamoDB table created: {DYNAMO_TABLE}")
    else:
        print(f"[SETUP] DynamoDB table exists: {DYNAMO_TABLE}")

    if DYNAMO_PAYMENTS not in existing:
        table2 = dynamodb.create_table(
            TableName=DYNAMO_PAYMENTS,
            KeySchema=[{"AttributeName": "violation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "violation_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )
        table2.wait_until_exists()
        print(f"[SETUP] DynamoDB table created: {DYNAMO_PAYMENTS}")
    else:
        print(f"[SETUP] DynamoDB table exists: {DYNAMO_PAYMENTS}")


def create_s3_bucket():
    s3 = get_client("s3")
    if not s3:
        print("[SETUP] Skipping S3 — no AWS credentials"); return

    try:
        if AWS_REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": AWS_REGION}
            )
        s3.put_public_access_block(
            Bucket=S3_BUCKET,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True
            }
        )
        print(f"[SETUP] S3 bucket created: {S3_BUCKET}")
    except Exception as e:
        if "BucketAlreadyOwnedByYou" in str(e) or "BucketAlreadyExists" in str(e):
            print(f"[SETUP] S3 bucket exists: {S3_BUCKET}")
        else:
            print(f"[SETUP] S3 error: {e}")


def create_sns_topic():
    """
    FIX: Creates SNS topic AND:
      - Auto-subscribes ALERT_PHONE_NUMBER for direct SMS
      - Auto-subscribes ALERT_EMAIL for email alerts
      - Writes SNS_TOPIC_ARN to .env automatically
    """
    sns = get_client("sns")
    if not sns:
        print("[SETUP] Skipping SNS — no AWS credentials"); return None

    resp      = sns.create_topic(Name="highbeam-violations",
                                  Attributes={"DisplayName": "High Beam Violation Alert"})
    topic_arn = resp["TopicArn"]
    print(f"[SETUP] SNS topic ARN: {topic_arn}")

    # FIX: auto-write to .env
    _update_env("SNS_TOPIC_ARN", topic_arn)

    # FIX: Subscribe ALERT_PHONE_NUMBER if configured
    alert_phone = os.getenv("ALERT_PHONE_NUMBER", "").strip()
    if alert_phone:
        try:
            r = sns.subscribe(
                TopicArn=topic_arn,
                Protocol="sms",
                Endpoint=alert_phone
            )
            print(f"[SETUP] SNS SMS subscription added: {alert_phone}")
        except Exception as e:
            print(f"[SETUP] SNS SMS subscribe failed: {e}")
    else:
        print("[SETUP] ALERT_PHONE_NUMBER not set — skipping SMS subscription")
        print("[SETUP]   Add ALERT_PHONE_NUMBER=+91XXXXXXXXXX to .env then re-run setup")

    # FIX: Subscribe ALERT_EMAIL if configured
    alert_email = os.getenv("ALERT_EMAIL", os.getenv("SES_FROM_EMAIL", "")).strip()
    if alert_email:
        try:
            r = sns.subscribe(
                TopicArn=topic_arn,
                Protocol="email",
                Endpoint=alert_email
            )
            print(f"[SETUP] SNS Email subscription pending: {alert_email}")
            print(f"[SETUP]   Check inbox and click 'Confirm subscription' link!")
        except Exception as e:
            print(f"[SETUP] SNS Email subscribe failed: {e}")

    return topic_arn


def setup_ses():
    """
    FIX: Check SES sender identity and print clear instructions if not verified.
    """
    ses_email = os.getenv("SES_FROM_EMAIL", "").strip()
    if not ses_email:
        print("[SETUP] SES_FROM_EMAIL not set — skipping SES setup"); return

    ses = get_client("ses")
    if not ses:
        print("[SETUP] Skipping SES — no AWS credentials"); return

    try:
        resp       = ses.list_verified_email_addresses()
        verified   = resp.get("VerifiedEmailAddresses", [])
        if ses_email in verified:
            print(f"[SETUP] SES email already verified: {ses_email}")
        else:
            ses.verify_email_address(EmailAddress=ses_email)
            print(f"[SETUP] SES verification email sent to: {ses_email}")
            print(f"[SETUP]   Open that email and click the verification link!")
            print(f"[SETUP]   (SES emails won't send until verified)")
    except Exception as e:
        print(f"[SETUP] SES setup error: {e}")


def create_iot_policy():
    iot = get_client("iot")
    if not iot:
        print("[SETUP] Skipping IoT Core — no AWS credentials"); return

    policy_doc = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["iot:Connect","iot:Publish","iot:Subscribe","iot:Receive"],
            "Resource": "*"
        }]
    })

    try:
        iot.create_policy(policyName="HighBeamESP32Policy", policyDocument=policy_doc)
        print("[SETUP] IoT Core policy created: HighBeamESP32Policy")
    except iot.exceptions.ResourceAlreadyExistsException:
        print("[SETUP] IoT Core policy already exists")
    except Exception as e:
        print(f"[SETUP] IoT policy error: {e}")

    try:
        endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        print(f"[SETUP] IoT Core endpoint: {endpoint}")
        # FIX: auto-write to .env
        _update_env("IOT_ENDPOINT", endpoint)
    except Exception as e:
        print(f"[SETUP] IoT endpoint error: {e}")


def setup_cloudwatch_alarm():
    cw = get_client("cloudwatch")
    if not cw:
        print("[SETUP] Skipping CloudWatch — no AWS credentials"); return

    try:
        cw.put_metric_alarm(
            AlarmName="HighBeamViolationSpike",
            AlarmDescription="Alert when violations exceed 10 per hour",
            MetricName="ViolationCount",
            Namespace="HighBeamDetection",
            Statistic="Sum",
            Period=3600,
            EvaluationPeriods=1,
            Threshold=10,
            ComparisonOperator="GreaterThanThreshold",
            TreatMissingData="notBreaching"
        )
        print("[SETUP] CloudWatch alarm created: HighBeamViolationSpike")
    except Exception as e:
        print(f"[SETUP] CloudWatch: {e}")


def verify_all_services():
    """FIX: After setup, verify each service is reachable."""
    print("\n[SETUP] Verifying services...")
    results = {}

    if aws_available():
        # DynamoDB
        try:
            db = get_resource("dynamodb")
            [t.name for t in db.tables.all()]
            results["DynamoDB"] = "OK"
        except Exception as e:
            results["DynamoDB"] = f"ERROR: {e}"

        # SNS
        try:
            sns = get_client("sns")
            sns.list_topics()
            results["SNS"] = "OK"
        except Exception as e:
            results["SNS"] = f"ERROR: {e}"

        # SES
        try:
            ses = get_client("ses")
            ses.get_send_quota()
            results["SES"] = "OK"
        except Exception as e:
            results["SES"] = f"ERROR: {e}"

        # S3
        try:
            s3 = get_client("s3")
            s3.head_bucket(Bucket=S3_BUCKET)
            results["S3"] = "OK"
        except Exception as e:
            results["S3"] = f"ERROR: {e}"

        # IoT Core
        iot_ep = os.getenv("IOT_ENDPOINT", "")
        results["IoT Core"] = "OK (endpoint set)" if iot_ep else "WARNING: IOT_ENDPOINT not set"
    else:
        results = {s: "SKIP (no AWS credentials)" for s in ["DynamoDB","SNS","SES","S3","IoT Core"]}

    for svc, status in results.items():
        icon = "OK" if "OK" in status else ("WARN" if "WARNING" in status else "FAIL")
        sym  = {"OK":"✓","WARN":"!","FAIL":"✗"}[icon]
        print(f"  [{sym}] {svc:<12} {status}")

    return results


if __name__ == "__main__":
    # Load .env first so getenv() picks up values
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        pass

    if not aws_available():
        print("[SETUP] AWS credentials not found in .env")
        print("[SETUP] Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY first")
        print("[SETUP] Get free credentials at: aws.amazon.com/free")
        sys.exit(1)

    print("=" * 55)
    print("  AWS RESOURCE SETUP — High Beam Detection System")
    print(f"  Region: {AWS_REGION}")
    print("=" * 55)

    create_dynamodb_tables()
    create_s3_bucket()
    create_sns_topic()
    setup_ses()
    create_iot_policy()
    setup_cloudwatch_alarm()
    verify_all_services()

    print("\n[SETUP] Done! Run: python start.py")
