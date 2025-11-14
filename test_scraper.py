import os
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup
from bu_scraper import parse_course_block, text, save_sqlite, scrape


def test_text_function():
    """Ensure text() safely extracts stripped text or returns empty string."""
    soup = BeautifulSoup("<span> Hello </span>", "html.parser")
    el = soup.find("span")
    assert text(el) == "Hello"
    assert text(None) == ""


def test_parse_course_block_valid():
    """Check that parse_course_block extracts course info correctly."""
    html = """
    <div class="bu_collapsible_container" id="course-1">
        <h5><span class="cf-course-id">MET CS 232</span> Programming with Java</h5>
        <table>
            <tr>
                <td>A1</td><td>Lecture</td><td>Shahossini</td>
                <td>WED 140</td><td>T</td><td>6:00–8:45 PM</td>
            </tr>
        </table>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    block = soup.find("div")
    num, name, rows = parse_course_block(block)

    assert num == "MET CS 232"
    assert "Programming" in name
    assert len(rows) == 1
    assert rows[0] == (
        "MET CS 232",
        "Programming with Java",
        "A1",
        "Shahossini",
        "WED 140",
        "T",
        "6:00–8:45 PM",
    )


def test_parse_course_block_missing_table():
    """Ensure missing table returns empty rows list."""
    html = '<div class="bu_collapsible_container"><h5><span class="cf-course-id">MET CS 101</span> Intro</h5></div>'
    soup = BeautifulSoup(html, "html.parser")
    num, name, rows = parse_course_block(soup.find("div"))
    assert num == "MET CS 101"
    assert name == "Intro"
    assert rows == []


def test_save_sqlite_creates_and_inserts(tmp_path):
    """Ensure save_sqlite creates a new DB and inserts rows."""
    db_path = tmp_path / "test.sqlite"
    rows = [
        ("MET CS 232","Programming with Java", "A1","Shahossini","WED 140", "T", "6:00 pm –8:45 pm",),
        ("MET CS 342", "Data Structures with Java", "A1", "Liang", "MCS B33", "R", "600 pm –8:45 pm"),
    ]
    save_sqlite(rows, db_path=str(db_path))

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM public_classes;")
    count = cur.fetchone()[0]
    con.close()
    assert count == 2


@patch("bu_scraper.requests.get")
@patch("bu_scraper.save_sqlite")
def test_scrape_makes_request_and_saves(mock_save, mock_get):
    """Ensure scrape() fetches, parses, and saves correctly."""
    mock_resp = MagicMock()
    mock_resp.text = """
        <div class="bu_collapsible_container" id="course-1">
            <h5><span class="cf-course-id">MET CS 232</span> Programming with Java</h5>
            <table>
                <tr>
                    <td>A1</td><td>Lecture</td><td>Shahossini</td>
                    <td>WED 140</td><td>T</td><td>6:00–8:45 PM</td>
                </tr>
            </table>
        </div>
        """
    mock_resp.raise_for_status = lambda: None
    mock_get.return_value = mock_resp

    scrape("http://fake-url.com")

    mock_get.assert_called_once()
    mock_save.assert_called_once()
    saved_rows = mock_save.call_args[0][0]
    assert len(saved_rows) == 1
    assert saved_rows[0][0] == "MET CS 232"