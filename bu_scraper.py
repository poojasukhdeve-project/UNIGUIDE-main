import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait

# ---- CONFIG ----
DB_PATH = "courses.sqlite"
PORTAL_URL = (
    "https://mybustudent.bu.edu/psp/BUPRD/EMPLOYEE/SA/s/"
    "WEBLIB_HCX_CM.H_COURSE_CATALOG.FieldFormula.IScript_Main?"
)

# ---- DB ----
DDL = """
CREATE TABLE IF NOT EXISTS classes_pretty (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    title TEXT,
    building TEXT,
    room TEXT,
    days TEXT,
    time TEXT,
    instructor TEXT
);
"""
def init_db(path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute(DDL)
    con.commit()
    return con

def insert_row(con: sqlite3.Connection, row: Tuple[str, str, str, str, str, str, str]):
    con.execute(
        """INSERT INTO classes_pretty
           (code, title, building, room, days, time, instructor)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        row,
    )

# ---- helpers ----
def g(d: Dict[str, Any], *keys, default: str = "") -> str:
    cur: Any = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return "" if cur is None else str(cur)

def build_code(course_obj: Dict[str, Any]) -> str:
    subject = g(course_obj, "subject") or g(course_obj, "SUBJECT")
    num = g(course_obj, "catalogNbr") or g(course_obj, "CATALOG_NBR")
    return (subject + " " + num).strip()

def build_title(course_obj: Dict[str, Any]) -> str:
    return g(course_obj, "descr") or g(course_obj, "DESCR") or g(course_obj, "title")

DAY_MAP = {
    "M": "Mon", "MO": "Mon",
    "T": "Tue", "TU": "Tue", "TUE": "Tue",
    "W": "Wed",
    "R": "Thu", "TH": "Thu",
    "F": "Fri",
    "S": "Sat",
    "U": "Sun",
}

def normalize_days(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip().upper().replace(" ", "")
    # Common encodings: "MWF", "TuTh", "TUTH", "MON/WED", "M/W"
    # Split on separators first
    if "/" in raw:
        parts = raw.split("/")
    else:
        # Try to tokenize pairs like TUTH -> TU + TH
        parts = []
        i = 0
        while i < len(raw):
            # Lookahead 2 letters first (TU, TH)
            if i + 1 < len(raw) and raw[i:i+2] in ("TU", "TH"):
                parts.append(raw[i:i+2])
                i += 2
            else:
                parts.append(raw[i])
                i += 1
    human = [DAY_MAP.get(p, p.title()) for p in parts if p]
    # Deduplicate while preserving order
    seen = set(); out = []
    for d in human:
        if d not in seen:
            seen.add(d); out.append(d)
    return "/".join(out)

def _to_12h(t: str) -> str:
    """Convert '1800' or '18:00' or '6:00 PM' to '6:00 PM'."""
    t = t.strip()
    # Already looks like 12h
    if re.search(r"(AM|PM)$", t, re.I):
        # Normalize spacing/case
        t = re.sub(r"\s*(AM|PM)$", lambda m: " " + m.group(1).upper(), t, flags=re.I)
        return t
    # 24h HHMM
    m = re.fullmatch(r"(\d{2})(\d{2})", t)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
        ap = "AM" if hh < 12 else "PM"
        h12 = hh % 12 or 12
        return f"{h12}:{mm:02d} {ap}"
    # 24h HH:MM
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
        ap = "AM" if hh < 12 else "PM"
        h12 = hh % 12 or 12
        return f"{h12}:{mm:02d} {ap}"
    return t  # fallback

def format_time(start: str, end: str, packed: str) -> str:
    """Prefer explicit start/end; fallback to packed string."""
    if start or end:
        s = _to_12h(start) if start else ""
        e = _to_12h(end) if end else ""
        return f"{s} - {e}".strip(" -")
    return _to_12h(packed)

LOC_PATTERNS = [
    re.compile(r"^\s*([A-Za-z]{2,5})[\s\-]?(\d{1,4}[A-Za-z]?)\s*$"),  # FLR 123 or FLR-123 or FLR123
    re.compile(r"^\s*([A-Za-z]{2,5})\s+(.+)$"),                        # FLR 123A West
]

def split_building_room(loc: str) -> Tuple[str, str]:
    loc = (loc or "").strip()
    for pat in LOC_PATTERNS:
        m = pat.match(loc)
        if m:
            b = m.group(1).upper().strip()
            r = m.group(2).strip()
            return b, r
    # If contains space, try last token as room
    if " " in loc:
        parts = loc.split()
        return parts[0].upper(), " ".join(parts[1:])
    return "", loc  # unknown format

def extract_rows_pretty(con: sqlite3.Connection, data: Any):
    """
    Walk likely shapes and insert rows with: code, title, building, room, days, time, instructor
    """
    def handle_course(c: Dict[str, Any]):
        code = build_code(c)
        title = build_title(c) or code
        classes = c.get("classes") or c.get("CLASSES") or []
        if isinstance(classes, list) and classes:
            for cls in classes:
                meetings = cls.get("meetings") or cls.get("MEETINGS") or []
                if not isinstance(meetings, list) or not meetings:
                    insert_row(con, (code, title, "", "", "", "", ""))
                    continue
                for m in meetings:
                    days_raw = g(m, "days") or g(m, "MTG_DAYS")
                    start = g(m, "START_TIME")
                    end = g(m, "END_TIME")
                    packed_time = g(m, "time")
                    time_str = format_time(start, end, packed_time)

                    loc = g(m, "location") or g(m, "FACILITY_ID") or g(m, "LOCATION")
                    building, room = split_building_room(loc)

                    instr = g(m, "instructor") or g(m, "INSTRUCTOR_NAME") or g(m, "INSTRUCTOR")

                    insert_row(con, (code, title, building, room, normalize_days(days_raw), time_str, instr))
        else:
            insert_row(con, (code, title, "", "", "", "", ""))

    def walk(x: Any):
        if isinstance(x, dict):
            if isinstance(x.get("courses"), list):
                for c in x["courses"]:
                    if isinstance(c, dict): handle_course(c)
                return
            if isinstance(x.get("data"), list):
                for c in x["data"]:
                    if isinstance(c, dict): handle_course(c)
                return
            if any(k in x for k in ("subject","SUBJECT","catalogNbr","CATALOG_NBR","descr","DESCR")):
                handle_course(x); return
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for i in x: walk(i)

    walk(data)
    con.commit()

# ---- Browser fetch ----
def fetch_text_with_login(url: str, login_timeout_sec: int = 300) -> str:
    opts = webdriver.ChromiumOptions() if hasattr(webdriver, "ChromiumOptions") else webdriver.ChromeOptions()
    opts.add_argument("--disable-gpu")
    opts.add_argument("--start-maximized")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    try:
        driver.get(url)
        WebDriverWait(driver, login_timeout_sec).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        start = time.time()
        while True:
            cur = driver.current_url
            text = driver.execute_script("return document.body.innerText || ''") or ""
            # Stop when the iScript returns content that looks like JSON/plain
            if "/IScript_Main" in cur and (("{" in text) or ("[" in text) or ("null" in text)):
                break
            if time.time() - start > login_timeout_sec:
                raise TimeoutError("Login took too long—stay signed in and retry.")
            time.sleep(1)
        # Prefer <pre> (JSON endpoints often render it)
        try:
            pre = driver.find_element(By.TAG_NAME, "pre")
            raw = pre.text.strip()
            if raw:
                return raw
        except Exception:
            pass
        return (driver.execute_script("return document.body.innerText || ''") or "").strip()
    finally:
        driver.quit()

def main():
    con = init_db(DB_PATH)
    print("[INFO] Chrome will open—please sign in if prompted.")
    raw = fetch_text_with_login(PORTAL_URL)
    print(f"[INFO] Received {len(raw)} characters from portal.")
    try:
        data = json.loads(raw)
        extract_rows_pretty(con, data)
        print("[OK] Saved to classes_pretty with columns: code, title, building, room, days, time, instructor.")
    except Exception as e:
        print("[ERROR] Could not parse JSON. Copy the final URL (with filters/params) into PORTAL_URL. ->", e)
    finally:
        con.close()
        print(f"[DONE] Database: {DB_PATH}")

if __name__ == "__main__":
    main()
