---
title: SevaAgent
emoji: 🤖
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# SevaAgent

Voice or text in (English / Telugu / Hindi) -> eligibility + cited schemes -> AI browser agent fills the application on a live portal, recovers from errors, returns a receipt.

## Run (2 terminals)

**Terminal 1: backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # then add GROQ_API_KEY and GEMINI_API_KEY
export $(grep -v '^#' .env | xargs)
uvicorn main:app --port 8000
```

**Terminal 2: frontend**
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 (use Chrome for voice input).

## Demo script (3 min)
1. Pick Telugu, press **Speak**, say your profile. Click **Understand my details**.
2. Upload a photo of an income or caste certificate. Fields auto-fill with confidence.
3. **Find my schemes**: eligible schemes with reasons and fact-sheet citations. Press **Read aloud**.
4. **Apply with agent** on a scholarship: watch the live browser, the portal reject the form, the agent fix it, and the receipt.

## Without API keys
- No GROQ_API_KEY: English-only rule-based parsing, template summary, rule-based form filling and recovery.
- No GEMINI_API_KEY: certificate upload is disabled; enter details manually.
- The core flow (match + agent) still runs.

## Honest limits
- The agent applies on the **mock portal** (`backend/portal`), not real government sites. Real portals need OTP/captcha/Aadhaar login, which this must not bypass.
- Scheme data in `backend/schemes.json` is a summarised demo fact sheet. Verify limits and amounts on official portals before real use.
- Retrieval is a small BM25 index over those fact sheets (plus any `.txt`/`.md` you drop in `backend/data/docs/`). Swap in ChromaDB if you want embeddings.
- Models are set by `GROQ_MODEL` / `GEMINI_MODEL`; change them if a model name is retired.
- `SEVA_CHROME_PATH` lets Playwright use an installed Chrome if `playwright install` is blocked.

## Layout
- `backend/engine.py` rules + BM25, `llm.py` Groq/Gemini, `agent.py` Playwright agent, `portal/` mock portal, `main.py` API + SSE
- `frontend/src/App.jsx` UI
