"""LLM helpers: Groq (text/JSON) and Gemini (vision). Every call degrades gracefully without keys."""
import base64
import json
import logging
import os
import re
from datetime import date

import httpx

log = logging.getLogger("seva.llm")

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

LANG_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi"}

STATES = [
    "Telangana", "Andhra Pradesh", "Karnataka", "Maharashtra", "Tamil Nadu", "Kerala",
    "Gujarat", "Rajasthan", "Uttar Pradesh", "Madhya Pradesh", "Bihar", "West Bengal",
    "Odisha", "Punjab", "Haryana", "Delhi", "Assam", "Jharkhand", "Chhattisgarh",
]
OCCUPATIONS = ["student", "farmer", "self_employed", "artisan", "street_vendor", "employee", "homemaker", "unemployed", "other"]
CATEGORIES = ["SC", "ST", "BC", "OBC", "EBC", "General"]


def has_groq():
    return bool(os.getenv("GROQ_API_KEY"))


def has_gemini():
    return bool(os.getenv("GEMINI_API_KEY"))


async def groq_chat(system: str, user: str, json_mode=False, max_tokens=700):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    body = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "max_tokens": max_tokens + 1500, "reasoning_effort": "low",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content) if json_mode else content.strip()
    except Exception as e:  # network, quota, bad JSON
        log.warning("groq call failed: %s", e)
        return None


# ---------------- profile normalisation ----------------

def normalize_profile(d: dict) -> dict:
    out = {}
    if not isinstance(d, dict):
        return out
    for k in ("name", "state", "course", "institution", "dob"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    if out.get("state"):
        for s in STATES:
            if s.lower() == out["state"].lower():
                out["state"] = s
    try:
        if d.get("age") not in (None, ""):
            out["age"] = int(float(d["age"]))
    except (TypeError, ValueError):
        pass
    try:
        if d.get("annual_income") not in (None, ""):
            out["annual_income"] = int(float(str(d["annual_income"]).replace(",", "")))
    except (TypeError, ValueError):
        pass
    g = str(d.get("gender") or "").lower()
    if g in ("male", "female", "other"):
        out["gender"] = g
    o = str(d.get("occupation") or "").lower().replace(" ", "_").replace("-", "_")
    if o in OCCUPATIONS:
        out["occupation"] = o
    c = str(d.get("category") or "").strip()
    for cat in CATEGORIES:
        if c.lower() == cat.lower():
            out["category"] = cat
    if "age" not in out and out.get("dob"):
        a = age_from_dob(out["dob"])
        if a is not None:
            out["age"] = a
    return out


def age_from_dob(dob: str):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", dob or "")
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", dob or "")
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        born = date(y, mo, d)
    except ValueError:
        return None
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# ---------------- parsing user speech/text ----------------

PARSE_SYSTEM = (
    "Extract a citizen profile from the user's message, which may be in Telugu, Hindi or English. "
    "Return one JSON object with keys: name, age (integer), gender (male|female|other), state (Indian state in English), "
    "occupation (one of: student, farmer, self_employed, artisan, street_vendor, employee, homemaker, unemployed, other), "
    "category (SC, ST, BC, OBC, EBC or General), annual_income (integer rupees; 'X lakh' means X*100000), course. "
    "Use null for anything not stated. Never guess."
)


def heuristic_parse(text: str) -> dict:
    t = text.lower()
    d = {}
    m = re.search(r"(\d{1,2})\s*(?:years|year|yrs|yr)\b", t) or re.search(r"\bage\s*(?:is|:)?\s*(\d{1,2})\b", t) or re.search(r"\bi am (\d{1,2})\b", t)
    if m:
        d["age"] = int(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lakhs|lac|lacs)\b", t)
    if m:
        d["annual_income"] = int(float(m.group(1)) * 100000)
    else:
        m = re.search(r"(?:income|earn\w*|salary)\D{0,25}?(\d[\d,]{3,})", t) or re.search(r"(?:rs\.?|₹)\s?(\d[\d,]{3,})", t)
        if m:
            d["annual_income"] = int(m.group(1).replace(",", ""))
    for s in STATES:
        if s.lower() in t:
            d["state"] = s
            break
    if re.search(r"\b(female|woman|girl|she)\b", t):
        d["gender"] = "female"
    elif re.search(r"\b(male|man|boy|he)\b", t):
        d["gender"] = "male"
    if re.search(r"student|studying|b\.?\s?tech|m\.?\s?tech|college|degree|diploma", t):
        d["occupation"] = "student"
    elif re.search(r"farmer|farming|agricultur|cultivat", t):
        d["occupation"] = "farmer"
    elif re.search(r"street vendor|hawker|vendor", t):
        d["occupation"] = "street_vendor"
    elif re.search(r"artisan|carpenter|potter|weaver|blacksmith|tailor|goldsmith|cobbler", t):
        d["occupation"] = "artisan"
    elif re.search(r"own business|my shop|self.?employed|entrepreneur|small business", t):
        d["occupation"] = "self_employed"
    elif re.search(r"housewife|homemaker", t):
        d["occupation"] = "homemaker"
    m = re.search(r"\b(SC|ST|OBC|EBC|BC)\b", text)
    if m:
        d["category"] = m.group(1)
    elif "general category" in t:
        d["category"] = "General"
    m = re.search(r"\b(b\.?\s?tech|m\.?\s?tech|b\.?sc|m\.?sc|b\.?com|mbbs|diploma|intermediate)\b(?:\s+(?:in\s+)?(ece|cse|eee|mech|mechanical|civil|it|ai|ml))?", t)
    if m:
        d["course"] = m.group(0).strip().upper().replace(" IN ", " ")
    return d


async def parse_profile(text: str):
    data = await groq_chat(PARSE_SYSTEM, text, json_mode=True)
    if data is not None:
        return normalize_profile(data), "llm"
    return normalize_profile(heuristic_parse(text)), "rules"


# ---------------- vision: certificate extraction (Gemini) ----------------

EXTRACT_PROMPT = (
    "You are reading an Indian government certificate or ID (income certificate, caste certificate, marks memo, etc). "
    "Extract only what is printed. Return JSON with keys: name, dob (YYYY-MM-DD), gender (male|female|other), "
    "category (SC|ST|BC|OBC|EBC|General), annual_income (integer rupees), state, course, institution, "
    "confidence (object mapping each key you filled to a number 0-1). Use null when absent. Never guess or invent values."
)


async def extract_from_image(data: bytes, mime: str):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    body = {
        "contents": [{"parts": [{"text": EXTRACT_PROMPT}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": key},
                json=body,
            )
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            raw = json.loads(text)
    except Exception as e:
        log.warning("gemini call failed: %s", e)
        return None
    conf = raw.pop("confidence", None) if isinstance(raw, dict) else None
    return normalize_profile(raw), (conf if isinstance(conf, dict) else {})


# ---------------- explanation ----------------

async def explain(profile: dict, matches: list, lang: str) -> str:
    names = [m["name"] for m in matches[:5]]
    if not names:
        return "No matching schemes found for this profile. Try correcting the details."
    system = (
        f"You help Indian citizens understand government schemes. Reply in {LANG_NAMES.get(lang, 'English')}, in plain simple words, "
        "max 70 words. Mention the top schemes by name, the single most useful next step, and remind them to verify on the official portal. "
        "Do not invent amounts or rules beyond the facts given."
    )
    facts = json.dumps(
        [{"name": m["name"], "status": m["status"], "benefit": m["benefits"], "missing_info": m["missing"]} for m in matches[:5]],
        ensure_ascii=False,
    )
    out = await groq_chat(system, f"Profile: {json.dumps(profile, ensure_ascii=False)}\nMatches: {facts}", max_tokens=300)
    if out:
        return out
    eligible = [m["name"] for m in matches if m["status"] == "eligible"]
    likely = [m["name"] for m in matches if m["status"] == "likely"]
    parts = []
    if eligible:
        parts.append("You appear eligible for: " + ", ".join(eligible) + ".")
    if likely:
        parts.append("You may also qualify (more details needed): " + ", ".join(likely) + ".")
    parts.append("Check the documents list and verify on the official portal before applying.")
    return " ".join(parts)


# ---------------- speech to text (Groq Whisper) ----------------

STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3")


async def transcribe_audio(data: bytes, filename: str, mime: str, lang: str):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None, "GROQ_API_KEY is not set"
    form = {"model": STT_MODEL, "response_format": "json", "temperature": "0"}
    if lang in ("en", "te", "hi"):
        form["language"] = lang
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data=form,
                files={"file": (filename or "speech.webm", data, mime or "audio/webm")},
            )
            if r.status_code != 200:
                log.warning("groq stt failed: %s %s", r.status_code, r.text[:300])
                return None, f"Speech model error {r.status_code}: {r.text[:160]}"
            return (r.json().get("text") or "").strip(), None
    except Exception as e:
        log.warning("groq stt failed: %s", e)
        return None, f"Speech request failed: {e}"
