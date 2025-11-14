# -*- coding: utf-8 -*-
"""
CHATLOGUE.py (Uniguide backend, persistent DB)
Links directly to chatalogue.sqlite
"""

import os
import sqlite3
import datetime
import re
import requests
from openai import OpenAI

# -------------------------------
# 🔹 Configuration
# -------------------------------
CAMPUS_CITY = "Boston"
WEATHER_API_KEY = "880dbf81f1bbddf4865779b93ab2184b"
DB_NAME = "chatalogue.sqlite"

client = OpenAI(api_key="")

# -------------------------------
# 🔹 DB Setup & Session
# -------------------------------

def get_db_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, DB_NAME)

def init_db():
    return sqlite3.connect(get_db_path(), timeout=10, check_same_thread=False)

def get_session_id():
    """Fetch persistent session_id from DB."""
    with init_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT session_uuid FROM session_info LIMIT 1;")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("❌ No session_id found in DB — please run seed or migration first.")
        return row[0]

SESSION_ID = get_session_id()

# -------------------------------
# 🔹 Utility functions
# -------------------------------

def get_weather(city):
    """Fetch weather from OpenWeatherMap."""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url)
        data = response.json()
        if data.get("cod") != 200:
            return None
        desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        return (desc, temp)
    except Exception:
        return None

# -------------------------------
# 🔹 DB Access Helpers
# -------------------------------

def get_user_courses():
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT DISTINCT course_code FROM user_assignments WHERE session_id=?",
            (SESSION_ID,)
        ).fetchall()
        return [r["course_code"] for r in rows]

def get_course_info(course_code):
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM courses WHERE code=?", (course_code,)).fetchone()
        return dict(row) if row else None

def get_user_assignments(due_within_days=None, specific_title=None, course_code=None):
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        query = "SELECT course_code, title, due_date, status FROM user_assignments WHERE session_id=?"
        params = [SESSION_ID]
        if course_code:
            query += " AND course_code=?"
            params.append(course_code)
        if specific_title:
            query += " AND LOWER(title) LIKE ?"
            params.append(f"%{specific_title.lower()}%")
        if due_within_days:
            today = datetime.date.today()
            thresh = today + datetime.timedelta(days=due_within_days)
            query += " AND due_date<=?"
            params.append(thresh.isoformat())
        return [dict(r) for r in cur.execute(query, tuple(params)).fetchall()]

def get_exams(course_code=None):
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        query = "SELECT course_code, exam_type, exam_datetime, location FROM exams WHERE session_id=?"
        params = [SESSION_ID]
        if course_code:
            query += " AND course_code=?"
            params.append(course_code)
        exams = [dict(r) for r in cur.execute(query, tuple(params)).fetchall()]
    now = datetime.datetime.now()
    return [e for e in exams if datetime.datetime.fromisoformat(e["exam_datetime"]) > now]

def get_upcoming_events(days=7):
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        now = datetime.datetime.now()
        end = now + datetime.timedelta(days=days)
        rows = cur.execute(
            "SELECT title, start_datetime, location, url FROM events WHERE session_id=?",
            (SESSION_ID,)
        ).fetchall()
        events = []
        for r in rows:
            try:
                dt = datetime.datetime.fromisoformat(r["start_datetime"])
                if now <= dt <= end:
                    events.append(dict(r))
            except Exception:
                continue
        return sorted(events, key=lambda e: e["start_datetime"])

def get_current_alerts():
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        today = datetime.date.today().isoformat()
        rows = cur.execute(
            "SELECT title, url FROM police_alerts WHERE session_id=? AND alert_date>=?",
            (SESSION_ID, today)
        ).fetchall()
        return [dict(r) for r in rows]

# -------------------------------
# 🔹 Main Chat Logic
# -------------------------------

def chat_loop( user_input, session_id=None,conversation_history=None):
    """Process a single query and return bot response."""
    user_input = user_input.strip().lower()
    if not user_input:
        return ""

    courses = get_user_courses()
    mentioned_course = next((c for c in courses if c.lower().replace(" ", "") in user_input.replace(" ", "")), None)

    # GREETINGS
    if user_input in {"hi", "hello", "hey"}:
        return "Hello! How can I help you today?"

    # ASSIGNMENTS
    if any(k in user_input for k in ["assignment", "due", "deadline"]):
        if not mentioned_course and len(courses) > 0:
            mentioned_course = courses[0]

        if "tomorrow" in user_input:
            tasks = get_user_assignments(due_within_days=1, course_code=mentioned_course)
            if not tasks:
                return "No assignments due by tomorrow."
        elif "week" in user_input:
            tasks = get_user_assignments(due_within_days=7, course_code=mentioned_course)
            if not tasks:
                return "No assignments due this week."
        else:
            tasks = get_user_assignments(specific_title=None, course_code=mentioned_course)
            if not tasks:
                return "No pending assignments found."

        seen = set()
        unique_tasks = []
        for t in tasks:
            key = (t["course_code"], t["title"], t["due_date"])
            if key not in seen:
                seen.add(key)
                unique_tasks.append(t)

        lines = [f"- {t['course_code']}: {t['title']} (due {t['due_date']})" for t in unique_tasks]
        header = f"Assignments for {mentioned_course}:\n" if mentioned_course else "Assignments across your courses:\n"
        return header + "\n".join(lines)

    # EXAMS
    if any(k in user_input for k in ["exam", "midterm", "final"]):
        if not mentioned_course and len(courses) > 0:
            mentioned_course = courses[0]

        exams = get_exams(course_code=mentioned_course)
        if not exams:
            return "No upcoming exams found."

        seen = set()
        unique_exams = []
        for e in exams:
            key = (e["course_code"], e["exam_type"], e["exam_datetime"])
            if key not in seen:
                seen.add(key)
                unique_exams.append(e)
        unique_exams.sort(key=lambda x: x["exam_datetime"])

        lines = []
        for e in unique_exams:
            try:
                dt = datetime.datetime.fromisoformat(e["exam_datetime"])
                date_str = dt.strftime("%b %d, %Y %I:%M %p")
            except Exception:
                date_str = e["exam_datetime"]
            lines.append(f"- {e['exam_type']} for {e['course_code']} → {date_str} at {e['location']}")

        header = f"Upcoming exams for {mentioned_course}:\n" if mentioned_course else "Upcoming exams across your courses:\n"
        return header + "\n".join(lines)

    # COURSE INFO
    if any(k in user_input for k in ["class", "course", "lecture"]) or re.search(r'\bcs\s*-?\s*\d{3}\b', user_input, re.IGNORECASE):
        if not mentioned_course:
            match = re.search(r'\b(cs\s*-?\s*\d{3})\b', user_input, re.IGNORECASE)
            if match:
                mentioned_course = match.group(1).upper().replace(" ", "").replace("-", "")

        course_code = mentioned_course or (courses[0] if len(courses) == 1 else None)
        if not course_code:
            return "Please specify the course."

        info = get_course_info(course_code)
        if not info:
            return f"No info found for {course_code}."

        if "where" in user_input or "location" in user_input:
            loc = f"{info['building']} room {info['room']}"
            weather = get_weather(CAMPUS_CITY)
            if weather:
                desc, temp = weather
                return f"{course_code} is in {loc}. Weather in {CAMPUS_CITY}: {desc}, {temp}°C."
            return f"{course_code} is held in {loc}."

        if "when" in user_input or "time" in user_input or "day" in user_input:
            return f"{course_code} meets on {info['days']} at {info['time']}."

        return f"{course_code}: {info['title']} taught by {info['instructor']}."

    # EVENTS
    if "event" in user_input or "happening" in user_input:
        events = get_upcoming_events()
        if not events:
            return "No events coming up."
        lines = [f"- {e['title']} ({e['start_datetime']} at {e['location']})" for e in events]
        return "Upcoming campus events:\n" + "\n".join(lines)

    # ALERTS
    if any(k in user_input for k in ["alert", "emergency", "police"]):
        alerts = get_current_alerts()
        if not alerts:
            return "No current police alerts."
        return "\n".join([f"{a['title']} → {a['url']}" for a in alerts])

    # WEATHER
    if "weather" in user_input:
        w = get_weather(CAMPUS_CITY)
        if not w:
            return "Weather info unavailable."
        return f"The current weather in {CAMPUS_CITY} is {w[0]} at {w[1]}°C."

    # FALLBACK (OpenAI)
    try:
        messages = (conversation_history or []) + [{"role": "user", "content": user_input}]
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"(Error using GPT: {e})"

# -------------------------------
# 🔹 Integration Adapter for GUI
# -------------------------------

def process_user_input(user_message: str) -> str:
    """Adapter for GUI chat_window.py — clean wrapper around chat_loop."""
    try:
        return chat_loop(SESSION_ID, user_message)
    except Exception as e:
        return f"⚠️ Internal Error: {str(e)}"

# -------------------------------
# 🔹 CLI test mode
# -------------------------------
"""if __name__ == "__main__":
    print(f"Session ID: {SESSION_ID}")
    while True:
        msg = input("You: ")
        if msg.lower() in {"quit", "exit", "bye"}:
            break
        print("Bot:", chat_loop(SESSION_ID, msg))"""
