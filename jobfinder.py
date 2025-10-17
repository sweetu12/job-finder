import os
import json
import requests
import pandas as pd
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials

# ===== CONFIG: Load all secrets from environment =====
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SERPAPI_KEY = os.environ["SERPAPI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

# Keywords for filtering jobs
KEYWORDS = ["mechanical","manufacturing","automation","robotics",
            "CAD","SolidWorks","MATLAB","PLC","entry-level"]

# Google Sheet name
SHEET_NAME = "JobFinderData"

# ===== INIT APIs =====
# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Google Sheets via service account JSON from secret
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
    url = "https://api.jobright.ai/search?query=mechanical%20engineer&country=US"
    try:
        return requests.get(url, timeout=10).json().get("jobs", [])
    except:
        return []

def fetch_greenhouse():
    rss_feeds = [
        "https://boards.greenhouse.io/embed/job_board?for=tesla",
        "https://boards.greenhouse.io/embed/job_board?for=geappliances"
    ]
    jobs = []
    for feed in rss_feeds:
        r = requests.get(feed)
        jobs += r.text.split("data-mapped='true'")
    return jobs

def fetch_google_jobs():
    url = f"https://serpapi.com/search.json?engine=google_jobs&q=mechanical+engineer+United+States&api_key={SERPAPI_KEY}"
    jobs = []
    try:
        data = requests.get(url).json()
        for j in data.get("jobs_results", []):
            jobs.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": j.get("location"),
                "link": j.get("apply_options", [{}])[0].get("link",""),
                "snippet": j.get("description","")
            })
    except:
        pass
    return jobs

# Collect jobs from all sources
all_jobs = []
for fn in [fetch_jobright, fetch_greenhouse, fetch_google_jobs]:
    all_jobs += fn()

# ⚡ Fix: remove non-dict entries (e.g., Greenhouse RSS strings)
all_jobs = [j for j in all_jobs if isinstance(j, dict)]

# --- Filter by posting time (last hour)
from datetime import datetime, timezone, timedelta
one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
all_jobs = [j for j in all_jobs if j.get("date_posted") and datetime.fromisoformat(j["date_posted"].replace("Z", "+00:00")) >= one_hour_ago]

# --- Filter by experience / clearance
EXCLUDE_KEYWORDS = ["senior", "manager", "lead", "5+ years", "3-5 years", "clearance", "ITAR", "TS/SCI"]
INCLUDE_KEYWORDS = ["entry-level", "new grad", "recent graduate", "0-2 years", "junior"]

filtered_jobs = []
for j in all_jobs:
    text = (j.get("title","") + " " + j.get("snippet","")).lower()
    if any(k.lower() in text for k in EXCLUDE_KEYWORDS):
        continue
    if any(k.lower() in text for k in INCLUDE_KEYWORDS):
        filtered_jobs.append(j)

all_jobs = filtered_jobs

print(f"Fetched {len(all_jobs)} jobs total")

# ===== KEYWORD SCORING =====
for j in all_jobs:
    text = (j.get("title","") + " " + j.get("snippet","")).lower()
    j["kw_score"] = sum(k in text for k in KEYWORDS)

top_kw = sorted(all_jobs, key=lambda x: x["kw_score"], reverse=True)[:30]

# ===== GPT SCORING =====
prompt = "Rate each job 0–100 for suitability for a recent Mechanicalo/Manufacturing/Mechatronics graduate:\n\n"
for i,j in enumerate(top_kw):
    prompt += f"{i+1}. {j['title']} at {j.get('company','')} ({j.get('location','')})\n{j.get('snippet','')}\n\n"

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role":"user","content":prompt}],
)

print("GPT Scoring:\n", resp.choices[0].message.content)

# ===== SAVE TO GOOGLE SHEETS =====
df = pd.DataFrame(all_jobs)
sheet.append_rows(df.values.tolist())

# ===== TELEGRAM ALERTS =====
top5 = sorted(all_jobs, key=lambda x: x.get("kw_score",0), reverse=True)[:5]
for job in top5:
    msg = f"🎯 {job['title']} - {job.get('company','')} ({job.get('location','')})\n{job.get('link','')}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    )

print("Telegram alerts sent!")
