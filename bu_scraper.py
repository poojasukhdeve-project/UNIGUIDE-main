import sys
import sqlite3
import requests
from bs4 import BeautifulSoup

DB_PATH = "courses_metcs.sqlite"
TABLE_SQL = """
DROP TABLE IF EXISTS public_classes;
CREATE TABLE public_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_number TEXT,
    course_name   TEXT,
    section       TEXT,
    instructor    TEXT,
    location      TEXT,
    days          TEXT,
    times         TEXT
);
"""

def text(el):
    return el.get_text(strip=True) if el else ""

def parse_course_block(block):
    """
    From one <div class="bu_collapsible_container" id="course-..."> block,
    return (course_number, course_name, [section rows...]).
    """
    # h5 holds: <span class="cf-course-id">MET CS 232</span> Programming with Java
    h5 = block.find("h5")
    if not h5:
        return None, None, []

    num_tag = h5.find("span", class_="cf-course-id")
    course_number = text(num_tag)

    # course title is the remaining text in h5 after removing the number
    h5_full = h5.get_text(" ", strip=True)
    course_name = h5_full.replace(course_number, "", 1).strip(" \u2013-")  # trim dashes/space

    # find the first table under the block (sections table)
    table = block.find("table")
    rows = []
    if table:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            # Expect columns: Section | Type | Instructor | Location | Days | Times
            if len(tds) >= 6:
                section    = text(tds[0])
                instructor = text(tds[2])
                location   = text(tds[3])
                days       = text(tds[4])
                times      = text(tds[5])
                rows.append((course_number, course_name, section, instructor, location, days, times))
    return course_number, course_name, rows

def scrape(url):
    print(f"[INFO] Scraping: {url}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # each course lives in a collapsible container with id starting with "course-"
    course_blocks = soup.select("div.bu_collapsible_container[id^='course-']")
    all_rows = []

    for block in course_blocks:
        _, _, rows = parse_course_block(block)
        all_rows.extend(rows)

    print(f"[INFO] Found {len(all_rows)} rows")
    save_sqlite(all_rows)

def save_sqlite(rows, db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # recreate table fresh each run
    for stmt in TABLE_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s + ";")
    cur.executemany(
        "INSERT INTO public_classes (course_number, course_name, section, instructor, location, days, times) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()
    print(f"[DONE] Saved to {db_path}")

if __name__ == "__main__":
    # if len(sys.argv) != 2:
    #     print('Usage: python bu_course_scraper.py "https://www.bu.edu/met/degrees-certificates/bs-computer-science/"')
    #     sys.exit(1)
    url = "https://www.bu.edu/met/degrees-certificates/bs-computer-science/"
    data = scrape(url)
    # save_sqlite(data)
