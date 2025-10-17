import os
import json
import requests
import pandas as pd
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta

# ===== CONFIG: Load Secrets =====
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

SHEET_NAME = "JobFinderData"

# ===== INIT APIs =====
client = OpenAI(api_key=OPENAI_API_KEY)

creds_dict = json.loads(GOOGLE_CREDS_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gs_client = gspread.authorize(creds)
sheet = gs_client.open(SHEET_NAME).sheet1

# ===== FETCH JOBS (JobRight GraphQL) =====
def fetch_jobright():
    url = "https://www.jobright.ai/api/graphql"
    
    job_functions = [
        "Mechanical Engineer",
        "Mechanical Design Engineer",
        "Mechanical Hardware Engineer",
        "Robotics Engineer",
        "Robotics Design Engineer",
        "Manufacturing Engineer",
        "Electromechanical Engineer"
    ]
    
    query = """
    query GetJobs($filter: JobFilterInput!) {
      jobs(filter: $filter, limit: 50) {
        id
        title
        companyName
        location
        jobUrl
        description
        datePosted
      }
    }
    """
    
    variables = {
        "filter": {
            "keywords": " OR ".join(job_functions),
            "experienceLevels": ["Intern", "Entry Level", "New Grad"],
            "jobTypes": ["Full-time", "Contract"],
            "postedWithin": "1d",
            "country": "United States",
            "excludeIndustries": ["Aerospace", "Defense"]
        }
    }

    try:
        resp = requests.post(url, json={"query": query, "variables": variables}, timeout=20)
        if resp.status_code != 200:
            print("Jobright HTTP error:", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        jobs = data.get("data", {}).get("jobs", [])
        
        normalized = []
        for j in jobs:
            desc = j.get("description", "") or ""
            if any(k in desc.lower() for k in ["security clearance", "itar", "aerospace", "defense", "us citizen only"]):
                continue
            normalized.append({
                "title": j.get("title"),
                "company": j.get("companyName"),
                "location": j.get("location"),
                "link": j.get("jobUrl"),
                "snippet": desc,
                "source": "Jobright"
            })
        return normalized
    except Exception as e:
        print("Jobright error:", e)
        return []

# ===== Collect from all sources =====
all_jobs = fetch_jobright()
print(f"Fetched {len(all_jobs)} jobs total before GPT filtering.")

# ===== GPT POST-PROCESSING FILTER =====
def filter_entry_level_jobs(jobs):
    if not jobs:
        return []
    results = []
    for job in jobs:
        desc = (job.get("title", "") + "\n" + job.get("snippet", ""))[:4000]
        prompt = f"""
You are a hiring analyst. Respond ONLY with 'Yes' or 'No'.
Keep jobs that are clearly New Grad or Entry Level (0–2 years experience).
Remove jobs that:
- Require security clearance or ITAR
- Mention aerospace, defense, or military
- Require US citizenship exclusively
Job:
{desc}
"""
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=5
            )
            result = resp.choices[0].message.content.strip().lower()
            if "yes" in result:
                results.append(job)
        except Exception as e:
            print("GPT filtering error:", e)
    return results

filtered_jobs = filter_entry_level_jobs(all_jobs)
print(f"✅ {len(filtered_jobs)} jobs kept after GPT filtering.")

# ===== SAVE TO GOOGLE SHEETS =====
if filtered_jobs:
    df = pd.DataFrame(filtered_jobs)
    sheet.append_rows(df.values.tolist())

# ===== TELEGRAM ALERTS =====
top_jobs = filtered_jobs[:5]
for job in top_jobs:
    msg = f"🎯 {job['title']} - {job.get('company','')} ({job.get('location','')})\n{job.get('link','')}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    )

print("Telegram alerts sent successfully!")
