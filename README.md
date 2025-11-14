# 🎓 Chatalogue — University Chatbot Assistant

Chatalogue is a lightweight, student-focused chatbot that helps users retrieve university-related information (class times, days, instructor names, room numbers, upcoming exams/events, and simple alerts) through a friendly chat interface. The project is designed primarily to run locally using a SQLite database and a Tkinter GUI.

---

## 🌟 Highlights

- Local-first: stores structured university data in a local SQLite database (`chatalogue.sqlite`) included in the repository.
- Simple GUI: a Tkinter-based chat window for conversational queries (`chat_window.py`).
- Extensible backend: `chatalogue.py` contains the logic for parsing questions and querying the database.
- Optional online features: weather lookups and an optional OpenAI fallback are present in the code but are not required for basic offline functionality.

---

## 📁 Files in this repository (root)

- `README.md` — This file.
- `chat_window.py` — Main GUI interface (Tkinter). Handles user input, background threading, and displays responses.
- `chatalogue.py` — Backend logic: DB helpers, intent handling, response formatting, and optional online fallbacks.
- `chatalogue.sqlite` — Local SQLite database with tables used by the backend.
- `test_chat.py` — Unit tests for chat functionality (if present and runnable).
- `test_full.py` — Tiny integration / smoke test file.

---

## ⚙️ How it works (high-level)

1. The user types a message in the chat window (`chat_window.py`).
2. The GUI sends the message to the backend on a background thread so the UI remains responsive.
3. The backend (`chatalogue.py`) analyzes the message and runs SQL queries against `chatalogue.sqlite` to gather relevant data.
4. If local data cannot answer the question and APIs are configured, the backend can optionally call external services (OpenWeatherMap and an OpenAI fallback).
5. The formatted answer is returned to the GUI and displayed to the user.

---

## 🔧 Key implementation notes & gotchas

- Session ID requirement: `chatalogue.py` calls `get_session_id()` at import time and will raise a RuntimeError if no `session_uuid` row exists in the `session_info` table. Ensure your `chatalogue.sqlite` contains a `session_info` entry before launching the GUI.
- Database location: `chatalogue.sqlite` is expected at the repository root; `get_db_path()` in `chatalogue.py` computes the absolute path relative to the source file.
- Optional online services:
  - Weather: `chatalogue.py` references an OpenWeatherMap key constant; replace it with your API key to enable weather lookups.
  - OpenAI fallback: the code includes an OpenAI client usage path. Supply an API key and verify network access if you want fallback completions.
- Threading: `chat_window.py` runs backend calls on a separate thread to prevent the Tkinter mainloop from freezing. Avoid long blocking operations on the main thread.

---

## 🚀 How to run / clone

Clone your fork (recommended — you are the main contributor):
```bash
git clone 
https://github.com/artisticdrake/Chatalogue.git
cd Chatalogue
```

Then:

1. Verify you have Python 3.8+ installed.

2. (Optional) Install dependencies used by optional features:
   ```bash
   pip install requests openai
   ```
   - `requests` is used for weather lookups.
   - `openai` is required only if you intend to enable the OpenAI fallback in `chatalogue.py`.

3. Check the database:
   - The repository includes `chatalogue.sqlite`. Ensure it contains a `session_info` table with at least one `session_uuid`. If you need to inspect:
     ```bash
     sqlite3 chatalogue.sqlite
     sqlite> .tables
     sqlite> SELECT * FROM session_info LIMIT 1;
     ```
   - If `session_info` is missing, create/seed the DB or add a `session_uuid` row to avoid runtime errors.

4. Launch the chat UI:
   ```bash
   python chat_window.py
   ```

---


## 🛠 Development notes

- The supported and intended entrypoint for this repository is the combination of `chat_window.py` (frontend) and `chatalogue.py` (backend). There are no references to alternate Chatalogue variant files in this README.
- If you modify the DB schema, update `chatalogue.py` helpers accordingly.
- For reproducible onboarding, consider adding a `seed_db.py` or migration script that populates `chatalogue.sqlite` with a `session_info.session_uuid` and example rows.

---

## 👨‍🎓 Author & Contributors

Repository and project content are maintained by the Chatalogue contributors.
