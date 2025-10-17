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
SERPAPI_KEY = os.environ["SERPAPI_KEY"]
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

# ===== FETCH JOBS =====
def fetch_jobright():
    url = "https://api.jobright.ai/search"

    job_functions = [
        "Mechanical Engineer",
        "Mechanical Design Engineer",
        "Mechanical Hardware Engineer",
        "Robotics Engineer",
        "Robotics Design Engineer",
        "Manufacturing Engineer",
        "Electromechanical Engineer"
    ]
    job_types = ["Full-time", "Contract"]
    experience_levels = ["Intern", "New Grad", "Entry Level"]

    params = {
        "query": " OR ".join(job_functions),
        "country": "US",
        "job_type": ",".join(job_types),
        "experience_level": ",".join(experience_levels),
        "posted_within": "1h"
    }

    try:
        resp = requests.get(url, params=params, timeout=10).json()
        jobs = resp.get("jobs", [])
        normalized = []
        for j in jobs:
            desc = j.get("description", "") or ""
            # Pre-filter obvious exclusions
            if any(k in desc.lower() for k in ["security clearance", "itar", "aerospace", "defense", "us citizen only"]):
                continue
            normalized.append({
                "title": j.get("title"),
                "company": j.get("company"),
                "location": j.get("location"),
                "link": j.get("url"),
                "snippet": desc,
                "source": "Jobright"
            })
        return normalized
    except Exception as e:
        print("Jobright error:", e)
        return []

def fetch_google_jobs():
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_jobs",
        "q": "mechanical engineer OR robotics engineer OR manufacturing engineer entry level",
        "location": "United States",
        "api_key": SERPAPI_KEY,
        "posted": "last_hour"
    }
    jobs = []
    try:
        data = requests.get(url, params=params, timeout=10).json()
        for j in data.get("jobs_results", []):
            desc = j.get("description", "") or ""
            if any(k in desc.lower() for k in ["security clearance", "itar", "aerospace", "defense", "us citizen only"]):
                continue
            jobs.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": j.get("location"),
                "link": j.get("apply_options", [{}])[0].get("link", ""),
                "snippet": desc,
                "source": "Google Jobs"
            })
    except Exception as e:
        print("Google Jobs error:", e)
    return jobs

# Collect from all sources
all_jobs = []
for fn in [fetch_jobright, fetch_google_jobs]:
    all_jobs += fn()

print(f"Fetched {len(all_jobs)} jobs total before GPT filtering.")

# ===== GPT POST-PROCESSING FILTER =====
def filter_entry_level_jobs(jobs):
    """Use GPT to remove non-entry-level, ITAR, or defense-related roles."""
    if not jobs:
        return []
    
    results = []
    for job in jobs:
        desc = (job.get("title", "") + "\n" + job.get("snippet", ""))[:4000]
        prompt = f"""
You are screening job postings. Respond ONLY with 'Yes' or 'No'.
Keep only entry-level jobs (0–2 years experience).
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
                messages=[{"role":"user","content":prompt}],
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
