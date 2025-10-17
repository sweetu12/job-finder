import os
import json
import requests
import pandas as pd
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta

# ===== CONFIG: Load all secrets from environment =====
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SERPAPI_KEY = os.environ["SERPAPI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

# Google Sheet name
SHEET_NAME = "JobFinderData"

# ===== INIT APIs =====
client = OpenAI(api_key=OPENAI_API_KEY)

# Google Sheets auth with scopes
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
    
    # Filters
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
    experience_levels = ["Intern", "New Grad", "Entry-Level"]
    
    params = {
        "query": " OR ".join(job_functions),   # match multiple functions
        "country": "US",
        "job_type": ",".join(job_types),       # comma-separated
        "experience_level": ",".join(experience_levels),
        "posted_within": "1h"                  # only last hour
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10).json()
        jobs = resp.get("jobs", [])
        normalized = []
        for j in jobs:
            normalized.append({
                "title": j.get("title"),
                "company": j.get("company"),
                "location": j.get("location"),
                "link": j.get("url"),
                "snippet": j.get("description",""),
                "source": "Jobright"
            })
        return normalized
    except:
        return []

def fetch_google_jobs():
    url = f"https://serpapi.com/search.json"
    params = {
        "engine": "google_jobs",
        "q": "mechanical engineer",
        "location": "United States",
        "experience_level": "entry-level",
        "posted": "last_hour",
        "api_key": SERPAPI_KEY
    }
    jobs = []
    try:
        data = requests.get(url, params=params).json()
        for j in data.get("jobs_results", []):
            jobs.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": j.get("location"),
                "link": j.get("apply_options", [{}])[0].get("link",""),
                "snippet": j.get("description",""),
                "source": "Google Jobs"
            })
    except:
        pass
    return jobs

# Collect jobs
all_jobs = []
for fn in [fetch_jobright, fetch_google_jobs]:
    all_jobs += fn()

print(f"Fetched {len(all_jobs)} jobs total")

# ===== KEYWORD SCORING (cheap pre-filter) =====
KEYWORDS = ["mechanical","manufacturing","automation","robotics",
            "CAD","SolidWorks","MATLAB","PLC"]

for j in all_jobs:
    text = (j.get("title","") + " " + j.get("snippet","")).lower()
    j["kw_score"] = sum(k in text for k in KEYWORDS)

# Top 30 jobs for GPT scoring
top_kw = sorted(all_jobs, key=lambda x: x["kw_score"], reverse=True)[:30]

# ===== GPT-4o-mini Scoring =====
prompt = "Classify the following jobs for suitability for a recent Mechatronics/Mechanical graduate. Rate 0–100, higher = better fit.\n\n"
for i,j in enumerate(top_kw):
    prompt += f"{i+1}. {j['title']} at {j.get('company','')} ({j.get('location','')})\n{j.get('snippet','')}\n\n"

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role":"user","content":prompt}],
)

# Print GPT scoring
print("GPT Scoring:\n", resp.choices[0].message.content)

# ===== SAVE TO GOOGLE SHEETS =====
if all_jobs:
    df = pd.DataFrame(all_jobs)
    sheet.append_rows(df.values.tolist())

# ===== TELEGRAM ALERTS (Top 5 by keyword score) =====
top5 = sorted(all_jobs, key=lambda x: x.get("kw_score",0), reverse=True)[:5]
for job in top5:
    msg = f"🎯 {job['title']} - {job.get('company','')} ({job.get('location','')})\n{job.get('link','')}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    )

print("Telegram alerts sent!")
