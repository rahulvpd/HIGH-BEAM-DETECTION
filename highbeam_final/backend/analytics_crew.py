"""
analytics_crew.py - CrewAI Integration for Daily Traffic Briefings.
"""
import os
import sys
from html import escape

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import Agent, Task, Crew, Process

sys.path.insert(0, os.path.dirname(__file__))
from aws_database import get_stats, get_all_violations
from aws_config import get_client, aws_available

# Set the Nvidia API Key using OpenAI's environment variables
# so CrewAI automatically connects to Nvidia NIM
nv_key = os.getenv("NVIDIA_API_KEY")
if nv_key:
    os.environ["OPENAI_API_KEY"] = nv_key
else:
    # Look for standard OpenAI key or print warning
    if not os.getenv("OPENAI_API_KEY"):
        print("[CrewAI] WARNING: NVIDIA_API_KEY is not set in environment.")

os.environ.setdefault("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")



# The "openai/" prefix tells CrewAI's underlying litellm to use the OpenAI API format
MODEL_NAME = "openai/meta/llama-3.1-70b-instruct"
POLICE_REPORT_EMAIL = os.getenv("POLICE_REPORT_EMAIL", "rahulrockindian7@gmail.com")
USER_COPY_EMAIL = os.getenv("USER_COPY_EMAIL", os.getenv("ALERT_EMAIL", "rahuljuneten2002@gmail.com"))
USER_COPY_PLATES = [
    p.strip().upper()
    for p in os.getenv("USER_COPY_PLATES", "").split(",")
    if p.strip()
]

def generate_daily_report():
    print("[CrewAI] Fetching today's data from DynamoDB...")
    
    stats = get_stats()
    violations = get_all_violations(limit=15)
    
    data_summary = f"Overall Stats: {stats}\n\nRecent Violations:\n"
    for v in violations:
        data_summary += f"- Plate {v.get('plate_masked')} at {v.get('timestamp')}, Lux: {v.get('lux')}, Fine: Rs.{v.get('fine_amount')}\n"

    data_analyst = Agent(
        role='Senior Traffic Data Analyst',
        goal='Analyze the raw high-beam violation data from DynamoDB and identify critical patterns.',
        backstory="You are an expert traffic analyst working for the Tamil Nadu Police.",
        verbose=True,
        allow_delegation=False,
        llm=MODEL_NAME
    )

    report_writer = Agent(
        role='Traffic Department Communications Officer',
        goal='Take analytical insights and write a formal, easy-to-read daily briefing for the Chief of Police.',
        backstory="You are the lead communications officer. You write clear, professional, and actionable government reports.",
        verbose=True,
        allow_delegation=False,
        llm=MODEL_NAME
    )

    analysis_task = Task(
        description=f"Analyze the following traffic data:\n{data_summary}\n\nIdentify the total revenue generated, the number of repeat offenders, and peak violation metrics.",
        expected_output="A bulleted list of key statistical insights.",
        agent=data_analyst
    )

    writing_task = Task(
        description="Using the insights from the Senior Traffic Data Analyst, write a formal Daily Briefing email to the Chief of Police. Include a summary of operations, revenue, recommendations, and a well-formatted data table showing the top repeat offenders.",
        expected_output="A formal email ready to be sent via Amazon SES. It must contain an introductory paragraph, a text-based table of top offenders (using markdown/ascii table formatting), and a concluding recommendation.",
        agent=report_writer
    )

    traffic_crew = Crew(
        agents=[data_analyst, report_writer],
        tasks=[analysis_task, writing_task],
        process=Process.sequential
    )

    print(f"[CrewAI] Agents are analyzing the data using {MODEL_NAME} on Nvidia NIM...")
    result = traffic_crew.kickoff()
    return str(result)

def _ses_send(subject: str, text: str, html: str, to_addresses: list[str]) -> dict:
    if not aws_available():
        return {"success": False, "error": "AWS credentials are not configured"}

    sender = os.getenv("SES_FROM_EMAIL", "").strip()
    if not sender:
        return {"success": False, "error": "SES_FROM_EMAIL is not set"}

    ses = get_client("ses")
    if not ses:
        return {"success": False, "error": "Could not create SES client"}

    try:
        resp = ses.send_email(
            Source=sender,
            Destination={"ToAddresses": to_addresses},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": text},
                    "Html": {"Data": html},
                },
            },
        )
        return {"success": True, "message_id": resp["MessageId"], "to": to_addresses, "from": sender}
    except Exception as e:
        return {"success": False, "error": str(e), "to": to_addresses, "from": sender}

def send_police_report_email(report: str, recipient: str = POLICE_REPORT_EMAIL) -> dict:
    html = f"""
<html>
  <body style="font-family:Arial,sans-serif;max-width:780px;margin:auto;color:#222;">
    <h2 style="color:#0f766e;">HighBeam AI Daily Crew Report</h2>
    <pre style="white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;font-size:14px;line-height:1.45;">{escape(report)}</pre>
  </body>
</html>"""
    return _ses_send("HighBeam AI Daily Crew Report", report, html, [recipient])

def build_user_violation_summary(user_email: str = USER_COPY_EMAIL, user_plates: list[str] | None = None) -> str:
    plates = [p.strip().upper() for p in (user_plates or USER_COPY_PLATES) if p.strip()]
    violations = get_all_violations(limit=1000)
    if plates:
        violations = [v for v in violations if str(v.get("plate", "")).upper() in plates]
    else:
        violations = []

    lines = [
        "HighBeam AI - Individual User Violation Summary",
        f"Recipient: {user_email}",
        "",
    ]

    if not violations:
        lines.extend([
            "No individual vehicle records are configured or matched for this email.",
            "Ask the traffic desk to map this email to the user's plate number in USER_COPY_PLATES.",
        ])
        return "\n".join(lines)

    total_pending = sum(int(v.get("fine_amount", 0)) for v in violations if v.get("status") == "PENDING")
    total_paid = sum(int(v.get("fine_amount", 0)) for v in violations if v.get("status") == "PAID")
    lines.extend([
        f"Matched plates: {', '.join(plates) if plates else 'configured user plates'}",
        f"Total user violations: {len(violations)}",
        f"Pending amount: Rs.{total_pending}",
        f"Paid amount: Rs.{total_paid}",
        "",
        "Recent user violations:",
    ])
    for v in violations[:15]:
        lines.append(
            f"- {v.get('plate_masked')} | {v.get('timestamp')} | Lux {v.get('lux')} | "
            f"Fine Rs.{v.get('fine_amount')} | Status {v.get('status', 'PENDING')}"
        )
    return "\n".join(lines)

def send_user_violation_summary(user_email: str = USER_COPY_EMAIL, user_plates: list[str] | None = None) -> dict:
    summary = build_user_violation_summary(user_email, user_plates)
    html = f"""
<html>
  <body style="font-family:Arial,sans-serif;max-width:780px;margin:auto;color:#222;">
    <h2 style="color:#b91c1c;">HighBeam AI Individual User Copy</h2>
    <pre style="white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;font-size:14px;line-height:1.45;">{escape(summary)}</pre>
  </body>
</html>"""
    email = _ses_send("HighBeam AI - Your Violation Summary", summary, html, [user_email])
    return {"summary": summary, "email": email}

def generate_and_email_daily_report(
    recipient: str = POLICE_REPORT_EMAIL,
    user_email: str = USER_COPY_EMAIL,
    user_plates: list[str] | None = None,
) -> dict:
    report = generate_daily_report()
    police_email = send_police_report_email(report, recipient)
    user_copy = send_user_violation_summary(user_email, user_plates)
    return {"report": report, "police_email": police_email, "user_copy": user_copy}

if __name__ == "__main__":
    result = generate_and_email_daily_report()
    report = result["report"]
    print("\n==========================================")
    print("FINAL REPORT GENERATED BY CREW AI (NVIDIA NIM):")
    print("==========================================")
    print(report)
    print("\nPOLICE EMAIL RESULT:")
    print(result["police_email"])
    print("\nUSER COPY RESULT:")
    print(result["user_copy"]["email"])
