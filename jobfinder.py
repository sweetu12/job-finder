import os
import json
import requests
import pandas as pd
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials

# ===== CONFIG =====
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Google credentials.json path
GOOGLE_CREDS_PATH = "credentials.json"

KEYWORDS = ["mechanical","manufacturing","automation","robotics","Electromechanical"
            "CAD","SolidWorks","MATLAB","PLC","entry-level","GD&T","3D","Product Design"]

SHEET_NAME = "JobFinderData"

# ===== INIT APIs =====
client = OpenAI(api_key=OPENAI_API_KEY)
creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH)
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

all_jobs = []
for fn in [fetch_jobright, fetch_greenhouse, fetch_google_jobs]:
    all_jobs += fn()

print(f"Fetched {len(all_jobs)} jobs total")

# ===== KEYWORD SCORING =====
for j in all_jobs:
    text = (j.get("title","") + " " + j.get("snippet","")).lower()
    j["kw_score"] = sum(k in text for k in KEYWORDS)

top_kw = sorted(all_jobs, key=lambda x: x["kw_score"], reverse=True)[:30]

# ===== GPT SCORING =====
prompt = "Rate each job 0–100 for suitability for a recent Mechanical/Manufacturing/Mechatronics graduate:\n\n"
for i,j in enumerate(top_kw):
    prompt += f"{i+1}. {j['title']} at {j.get('company','')} ({j.get('location','')})\n{j.get('snippet','')}\n\n"

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role":"user","content":prompt}],
)

print(resp.choices[0].message.content)

# ===== SAVE TO GOOGLE SHEETS =====
df = pd.DataFrame(all_jobs)
sheet.append_rows(df.values.tolist())

# ===== TELEGRAM ALERTS =====
top5 = sorted(all_jobs, key=lambda x: x.get("kw_score",0), reverse=True)[:5]
for job in top5:
    msg = f"🎯 {job['title']} - {job.get('company','')} ({job.get('location','')})\n{job.get('link','')}"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                  data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
