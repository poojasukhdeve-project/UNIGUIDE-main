# -*- coding: utf-8 -*-
"""
CHATLOGUE.py (Uniguide backend, persistent DB)
Universal Confidence Routing:
Parser/Subject → Subject prompts/scoping → Confidence Router → (DB pre-gen) → RAG (final)
Links directly to chatalogue.sqlite
"""

import os
import sqlite3
import datetime
import re
import requests
from typing import Optional, Tuple, Dict, Any, List
from openai import OpenAI

# -------------------------------
# 🔹 Configuration
# -------------------------------
CAMPUS_CITY = "Boston"
WEATHER_API_KEY = "880dbf81f1bbddf4865779b93ab2184b"
DB_NAME = "chatalogue.sqlite"
OPENAI_MODEL = "gpt-3.5-turbo"   # swap to a newer model if you like

# 🔒 Replace with your secure key (env var recommended)
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
# 🔹 Session-scoped subject memory
# -------------------------------
SESSION_CACHE: Dict[str, Dict[str, Any]] = {}

def remember_subject(session_id: str, **kwargs):
    state = SESSION_CACHE.setdefault(session_id, {})
    state.update(kwargs)

def recall_subject(session_id: str, key: str, default=None):
    return SESSION_CACHE.get(session_id, {}).get(key, default)

# -------------------------------
# 🔹 Utility: Weather
# -------------------------------

def get_weather(city):
    """Fetch weather from OpenWeatherMap."""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url, timeout=8)
        data = response.json()
        if data.get("cod") != 200:
            return None
        desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        return (desc, temp)
    except Exception:
        return None

def quirky_weather_line(city: str, desc: str, temp_c: float) -> str:
    """Small GPT prompt to turn weather into a short, friendly suggestion."""
    prompt = (
        f"The weather in {city} is '{desc}' and {round(temp_c,1)}°C.\n"
        "Write ONE short, quirky, friendly suggestion based on that weather.\n"
        "If cold: suggest warm layers. If raining: umbrella warning. If sunny: cheerful vibe.\n"
        "Keep it under 15 words. No quotation marks."
    )
    try:
        r = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a witty, succinct campus assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return ""

# -------------------------------
# 🔹 DB Access Helpers
# -------------------------------

def get_user_courses() -> List[str]:
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT DISTINCT course_code FROM user_assignments WHERE session_id=?",
            (SESSION_ID,)
        ).fetchall()
        return [r["course_code"] for r in rows]

def get_course_info(course_code: str) -> Optional[Dict[str, Any]]:
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM courses WHERE code=?", (course_code,)).fetchone()
        return dict(row) if row else None

def get_user_assignments(due_within_days=None, specific_title=None, course_code=None):
    """
    Return ALL assignments for the course (past + future).
    NOTE: We ignore due_within_days by design (your request).
    """
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
        query += " ORDER BY due_date ASC"
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
        query += " ORDER BY exam_datetime ASC"
        exams = [dict(r) for r in cur.execute(query, tuple(params)).fetchall()]
    return exams  # include past + future for full context

def get_upcoming_events(days=90):
    # Make this generous; RAG will pick what it needs
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT title, start_datetime, location, url FROM events WHERE session_id=?",
            (SESSION_ID,)
        ).fetchall()
        events = []
        for r in rows:
            try:
                _ = datetime.datetime.fromisoformat(r["start_datetime"])
                events.append(dict(r))
            except Exception:
                continue
        return sorted(events, key=lambda e: e["start_datetime"])

def get_current_alerts():
    with init_db() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT title, url, alert_date FROM police_alerts WHERE session_id=?",
            (SESSION_ID,)
        ).fetchall()
        return [dict(r) for r in rows]

# -------------------------------
# 🔹 Parser & Subject Detection
# -------------------------------

SIMPLE_Q_WORDS = ("what", "which", "where", "when", "can", "will")
COMPLEX_Q_WORDS = ("how", "why", "explain", "help", "plan", "summarize", "find", "show", "list")

SUBJECT_WORDS = {
    "assignment": ("assignment", "assignments", "hw", "homework", "project", "deadline", "due"),
    "exam":       ("exam", "midterm", "final", "test", "quiz"),
    "course":     ("course", "class", "lecture"),
    "event":      ("event", "happening", "meetup", "fair"),
    "alert":      ("alert", "emergency", "police"),
    "weather":    ("weather",),
    # NEW schedule words incl. abbreviations:
    "schedule": (
        "schedule", "timetable",
        "mon", "monday",
        "tue", "tues", "tuesday", "tu",
        "wed", "weds", "wednesday",
        "thu", "thur", "thurs", "thursday", "th",
        "fri", "friday",
        "sat", "saturday",
        "sun", "sunday"
    ),
}



COURSE_CODE_REGEX = re.compile(r'\b(cs\s*-?\s*\d{3})\b', re.IGNORECASE)

def parse_intent(user_input: str) -> Tuple[Optional[str], str]:
    """
    Returns (intent, complexity) — complexity is kept only for logs/features.
    """
    text = user_input.lower()
    complexity = "simple" if any(w in text for w in SIMPLE_Q_WORDS) else "complex"
    if any(w in text for w in COMPLEX_Q_WORDS):
        complexity = "complex"

    for intent, words in SUBJECT_WORDS.items():
        if any(w in text for w in words):
            return intent, complexity

    if COURSE_CODE_REGEX.search(text):
        return "course", complexity

    return None, complexity

WEEKDAY_ALIASES = {
    "monday":    ["mon", "monday"],
    "tuesday":   ["tue", "tues", "tuesday", "tu"],
    "wednesday": ["wed", "weds", "wednesday"],
    "thursday":  ["thu", "thur", "thurs", "thursday"],
    "friday":    ["fri", "friday"],
    "saturday":  ["sat", "saturday"],
    "sunday":    ["sun", "sunday"],
}

def _find_weekday(text: str) -> Optional[str]:
    t = text.lower()
    for canon, variants in WEEKDAY_ALIASES.items():
        if any(re.search(rf'\b{re.escape(v)}\b', t) for v in variants):
            return canon
    return None

def extract_subject(user_input: str) -> Dict[str, Any]:
    text = user_input.lower()
    subj: Dict[str, Any] = {}
    m = COURSE_CODE_REGEX.search(text)
    if m:
        subj["course_code"] = m.group(1).upper().replace(" ", "").replace("-", "")

    # weekday capture
    wd = _find_weekday(text)
    if wd:
        subj["day_of_week"] = wd

    # keep time window parsing (used for flavoring; not for filtering assignments)
    if "tomorrow" in text:
        subj["time_window"] = ("tomorrow", 1)
    elif "this week" in text or "week" in text:
        subj["time_window"] = ("week", 7)
    elif "today" in text:
        subj["time_window"] = ("today", 1)
    return subj


# -------------------------------
# 🔹 Simple DB Replies (pre-gen)
# -------------------------------

def reply_with_db(intent: str, subject: Dict[str, Any]) -> Optional[str]:
    """
    Deterministic DB-based replies for structured queries.
    NOTE: always returns ALL assignments; exams/events/alerts/courses as-is.
    """
    course_code = subject.get("course_code") or recall_subject(SESSION_ID, "active_course")
    
    # NEW: day-of-week schedule query (e.g., "what classes are on tuesday?")
    if intent == "schedule" or subject.get("day_of_week"):
        day = subject.get("day_of_week")
        if not day:
            return "Which day should I check? (e.g., Tuesday)"

        # Search patterns to match common encodings: "Tue", "Tu", "T", "Tue/Thu", etc.
        # We prioritize readable 3-letter matches and also try 'tu' for Tuesday specifically.
        like_patterns = {
            "monday":    ["%mon%"],
            "tuesday":   ["%tue%", "%tu%"],
            "wednesday": ["%wed%"],
            "thursday":  ["%thu%", "%thur%", "%thurs%"],
            "friday":    ["%fri%"],
            "saturday":  ["%sat%"],
            "sunday":    ["%sun%"],
        }[day]

        with init_db() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Optional scoping by active course (if you really want "classes for my active course on X")
            # but generally users mean "all classes on X", so we DO NOT filter by course_code here.
            # Build OR conditions for the patterns
            where_clauses = " OR ".join(["LOWER(days) LIKE ?"] * len(like_patterns))
            rows = cur.execute(
                f"SELECT * FROM courses WHERE {where_clauses}",
                tuple(p.lower() for p in like_patterns)
            ).fetchall()

        if not rows:
            return f"No classes on {day.title()}."

        lines = [
            f"- {r['code']}: {r['title']} — {r['days']} at {r['time']} in {r['building']} {r['room']} (Instructor: {r['instructor']})"
            for r in rows
        ]
        return f"Classes on {day.title()}:\n" + "\n".join(lines)
    if intent == "assignment":
        tasks = get_user_assignments(course_code=course_code)  # all records
        if not tasks:
            return "No assignments on record."
        lines = [f"- {t['course_code']}: {t['title']} (due {t['due_date']}, status: {t['status']})" for t in tasks]
        return "All assignments:\n" + "\n".join(lines)

    if intent == "exam":
        exams = get_exams(course_code=course_code)
        if not exams:
            return "No exams on record."
        lines = []
        for e in exams:
            try:
                dt = datetime.datetime.fromisoformat(e["exam_datetime"])
                date_str = dt.strftime("%b %d, %Y %I:%M %p")
            except Exception:
                date_str = e["exam_datetime"]
            lines.append(f"- {e['exam_type']} for {e['course_code']} → {date_str} at {e['location']}")
        return "All exams:\n" + "\n".join(lines)

    if intent == "event":
        events = get_upcoming_events()
        if not events:
            return "No events on record."
        lines = [f"- {e['title']} ({e['start_datetime']} at {e['location']})" for e in events]
        return "Campus events:\n" + "\n".join(lines)

    if intent == "alert":
        alerts = get_current_alerts()
        if not alerts:
            return "No police alerts on record."
        return "Police alerts:\n" + "\n".join([f"- {a['title']} ({a.get('alert_date','')}) → {a['url']}" for a in alerts])

    if intent == "weather":
        w = get_weather(CAMPUS_CITY)
        if not w:
            return "Weather info unavailable."
        tip = quirky_weather_line(CAMPUS_CITY, w[0], w[1]) or ""
        return f"Weather in {CAMPUS_CITY}: {w[0]}, {w[1]}°C. {tip}".strip()

    if intent == "course":
        if course_code:
            info = get_course_info(course_code)
            if not info:
                return f"I couldn’t find details for {course_code}."
            loc = f"{info['building']} room {info['room']}"
            return f"{course_code}: {info['title']} — {info['days']} at {info['time']} in {loc} (Instructor: {info['instructor']})."
        codes = get_user_courses()
        if not codes:
            return "No courses found in your profile."
        details = []
        for c in codes:
            info = get_course_info(c)
            if info:
                details.append(
                    f"{c}: {info['title']} — {info['days']} at {info['time']} in {info['building']} {info['room']} (Instructor: {info['instructor']})"
                )
        return "\n".join(details) if details else "No detailed course info available."

    return None

# -------------------------------
# 🔹 Universal Confidence Router (DB vs RAG)
# -------------------------------

ALLOWED_RAG_THEMES = [
    # academics / logistics
    "course", "class", "lecture", "assignment", "homework", "project", "exam", "midterm", "final", "test", "quiz",
    "deadline", "due", "syllabus", "office hours", "professor", "instructor", "room", "building", "location",
    "schedule", "time", "where", "when",
    # campus life
    "event", "happening", "police", "alert", "weather", "campus", "library",
    # study help
    "help", "improve", "study", "notes", "slides", "resources", "material", "tips", "prepare", "practice"
]

def _looks_in_scope(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ALLOWED_RAG_THEMES) or COURSE_CODE_REGEX.search(t) is not None

def calc_confidence(text: str, subject=None, active_course=None):
    """
    Assign a confidence score for how 'structured' (DB-appropriate) the query feels
    and return route in {'DB','RAG'} only. (RAG is the fallback.)
    """
    text = text.lower()
    score = 0.0
    features = {}

    # Positive (structured) signals
    if re.search(r"\b(cs|ma|ec|en|bu|phy|bio)\s*-?\s*\d{3}\b", text):
        score += 0.5; features["course_code"] = +0.5

    if any(k in text for k in [
        "when", "due", "date", "time", "deadline", "tomorrow", "week", "next", "today",
        "where", "room", "building", "location",
        "monday","tuesday","wednesday","thursday","friday","saturday","sunday"
    ]):
        score += 0.3; features["temporal_spatial"] = +0.3

    # NEW: explicit weekday parsed from extract_subject()
    if subject and subject.get("day_of_week"):
        score += 0.25; features["weekday_detected"] = +0.25

    if subject and subject.get("course_code"):
        score += 0.2; features["subject_present"] = +0.2

    if active_course:
        score += 0.1; features["active_course_memory"] = +0.1

    # Negative (conceptual/advisory) signals
    if any(k in text for k in ["help", "improve", "struggling", "guide", "prepare", "tips", "advice", "better", "optimize", "explain"]):
        score -= 0.5; features["advisory"] = -0.5

    if any(k in text.split()[:3] for k in ["how", "why", "should", "can", "could"]):
        score -= 0.3; features["question_type"] = -0.3

    if len(text.split()) > 18:
        score -= 0.1; features["length_penalty"] = -0.1

    score = max(min(score, 1.0), -1.0)

    # Slightly lower threshold to favor DB on concise, structured asks
    route = "DB" if score > 0.40 else "RAG"
    debug_log(f"[CONFIDENCE] total={score:.2f} → {route}")
    debug_log(f"[CONFIDENCE-FEATURES] {features}")
    return score, route


# -------------------------------
# 🔹 RAG Engine (retrieve → generate)
# -------------------------------

def apply_rag(user_input: str, intent: Optional[str], subject: Dict[str, Any], db_snippet: Optional[str] = None) -> str:
    """
    Main synthesis. Enforces scope. Uses ALL available context.
    DB pre-gen (if any) is injected as top context for smoother, human output.
    """
    text = user_input.strip()
    if not _looks_in_scope(text):
        debug_log("[RAG-GUARD] Out-of-scope → reject politely")
        return ("I'm your campus buddy, not a crystal ball 😊. "
                "I can help with BU courses, assignments, exams, events, alerts, or study tips.")

    course_code = subject.get("course_code") or recall_subject(SESSION_ID, "active_course")

    # Build rich context
    chunks = []
    if db_snippet:
        chunks.append(f"[DB_PREGEN]\n{db_snippet}")

    # Course-first
    if course_code:
        info = get_course_info(course_code)
        if info:
            chunks.append(f"[COURSE]\n{course_code}: {info['title']} — {info['days']} {info['time']} @ {info['building']} {info['room']} | Instructor: {info['instructor']}")

    # Assignments (ALL)
    if course_code:
        a = get_user_assignments(course_code=course_code)
    else:
        a = get_user_assignments()  # all courses
    if a:
        lines = [f"{x['course_code']} • {x['title']} • due {x['due_date']} • status {x['status']}" for x in a]
        chunks.append("[ASSIGNMENTS]\n" + "\n".join(lines))

    # Exams (ALL, ordered)
    e = get_exams(course_code=course_code) if course_code else get_exams()
    if e:
        lines = []
        for row in e:
            lines.append(f"{row['course_code']} • {row['exam_type']} • {row['exam_datetime']} • {row['location']}")
        chunks.append("[EXAMS]\n" + "\n".join(lines))

    # Events / Alerts (broad)
    ev = get_upcoming_events()
    if ev:
        chunks.append("[EVENTS]\n" + "\n".join(f"{x['title']} • {x['start_datetime']} • {x['location']}" for x in ev))
    al = get_current_alerts()
    if al:
        chunks.append("[ALERTS]\n" + "\n".join(f"{x['title']} • {x.get('alert_date','')} • {x['url']}" for x in al))

    # Weather (optional flavor)
    w = get_weather(CAMPUS_CITY)
    if w:
        chunks.append(f"[WEATHER]\n{CAMPUS_CITY}: {w[0]} • {w[1]}°C")

    context = "\n\n".join(chunks) if chunks else "NO_CONTEXT"

    prompt = (
        "You are a friendly Boston University assistant. "
        "Answer ONLY about campus/academics/study help. "
        "Use the CONTEXT to ground facts. If something is unknown, say so briefly. "
        "Write a natural 2–4 sentence answer, concise and helpful. "
        "If user asked for logistics (where/when), include clear specifics from CONTEXT.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"USER INPUT:\n{text}\n\n"
        "FINAL ANSWER:"
    )

    try:
        r = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Be concise, warm, and specific to the context."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        debug_log(f"[RAG-ERROR] {e}")
        # Last-ditch minimal reply if API fails
        return "I couldn’t generate a response just now."

# -------------------------------
# 🔹 Output generator
# -------------------------------

def output_gen(text: str) -> str:
    return (text or "").strip() or "I couldn’t generate a response this time."

# -------------------------------
# 🔹 Debug & Memory Helpers
# -------------------------------

DEBUG = True

def debug_log(*args):
    if DEBUG:
        print("[DEBUG]", *args)

def get_active_course(session_id: str):
    return recall_subject(session_id, "active_course")

def set_active_course(session_id: str, course_code: str):
    remember_subject(session_id, active_course=course_code)
    debug_log(f"[MEMORY] Active course set → {course_code}")

def clear_active_course(session_id: str):
    st = SESSION_CACHE.get(session_id, {})
    if "active_course" in st:
        del st["active_course"]
        debug_log("[MEMORY] Active course cleared")

# -------------------------------
# 🔸 Chat loop (Universal routing only)
# -------------------------------

def chat_loop(session_id, user_input, conversation_history=None):
    """Single universal router → DB or RAG (RAG is final output; DB is just a pre-gen context)."""
    if not user_input or not user_input.strip():
        return ""

    text = user_input.strip()
    low = text.lower()
    debug_log(f"INPUT: {text}")

    # Parse
    intent, complexity = parse_intent(text)
    subject = extract_subject(text)
    # If a weekday was mentioned, treat this as a schedule query
    if subject.get("day_of_week"):
        intent = "schedule"

    debug_log(f"[PARSER] intent={intent}, complexity={complexity}, subject={subject}")

    # Memory
    active_course = get_active_course(session_id)
    if active_course:
        debug_log(f"[MEMORY] active_course (before) = {active_course}")
    if subject.get("course_code"):
        course_code = subject["course_code"]
        info = get_course_info(course_code)
    
        if not info:
            debug_log(f"[VALIDATION] {course_code} not found in DB → skipping activation")
            return f"I couldn’t find {course_code} in your registered courses. Please check the course code or try another."
    
        if course_code != active_course:
            set_active_course(session_id, course_code)
            active_course = course_code
        else:
            debug_log("[MEMORY] Same course mentioned; context unchanged")

    if intent:
        remember_subject(session_id, last_intent=intent)
        debug_log(f"[MEMORY] remembered intent={intent}")
        
    # ---------- INTENT CONTINUITY FIX ----------
    last_intent = recall_subject(session_id, "last_intent")
    
    # If user only provided a new course and last intent exists, reuse it
    if (
        subject.get("course_code") 
        and not any(k in low for k in ["exam", "assignment", "event", "alert", "course"])
        and last_intent in ("exam", "assignment", "course", "event")
    ):
        debug_log(f"[INTENT CONTINUITY] Using previous intent='{last_intent}' for course correction")
        intent = last_intent
    
    # Quick commands
    if low in {"reset course", "clear course", "change course"}:
        clear_active_course(session_id)
        return "Course context cleared. Which course should I use next?"

    if low in {"hi", "hello", "hey"}:
        debug_log("[FASTPATH] Greeting")
        return "Is there anything else I can help you with today?"

    # Subject prompts
    if intent == "course" and not subject.get("course_code") and not active_course and not subject.get("day_of_week"):
        debug_log("[PROMPT] Course info requested but no course specified or active")
        return "Sure — which course? (e.g., CS101)"


    if intent in ("assignment", "exam") and not subject.get("course_code") and active_course:
        subject["course_code"] = active_course
        debug_log(f"[AUTO-SCOPE] {intent} → {active_course}")

    if intent in ("assignment", "exam") and not subject.get("course_code") and not active_course:
        debug_log(f"[PROMPT] {intent} requested but no course specified or active")
        return f"Which course should I check for {intent}s? (e.g., CS101)"

    # Universal routing
    score, route = calc_confidence(text, subject, active_course)
    debug_log(f"[ROUTER] Final route → {route}")
    # Force DB route for schedule queries
    if intent == "schedule":
        debug_log("[ROUTER-OVERRIDE] Schedule intent detected → Forcing DB route")
        route = "DB"

    # SIMPLE (DB) → still pass to RAG for refinement
    db_snippet = None
    if route == "DB":
        pre = reply_with_db(intent or "course", subject)
        db_snippet = pre or ""
        if pre:
            debug_log("[FLOW] SIMPLE(DB) pre-gen prepared; feeding into RAG for refinement")
            debug_log(f"[SIMPLE-OUTPUT]\n{pre}\n[END SIMPLE]")
        else:
            debug_log("[FLOW] SIMPLE(DB) had no rows; proceeding with RAG only")

    # COMPLEX (RAG final; also runs after SIMPLE pre-gen)
    rag_answer = apply_rag(text, intent, subject, db_snippet=db_snippet)
    debug_log("[FLOW] COMPLEX(RAG) produced final answer")
    return output_gen(rag_answer)

# -------------------------------
# 🔹 Integration Adapter for GUI
# -------------------------------

def process_user_input(user_message: str) -> str:
    try:
        return chat_loop(SESSION_ID, user_message)
    except Exception as e:
        return f"⚠️ Internal Error: {str(e)}"

# -------------------------------
# 🔹 CLI test mode
# -------------------------------
if __name__ == "__main__":
    print(f"Session ID: {SESSION_ID}")
    while True:
        msg = input("You: ")
        if msg.lower() in {"quit", "exit", "bye"}:
            break
        print("Bot:", chat_loop(SESSION_ID, msg))
