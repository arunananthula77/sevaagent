import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import engine
import llm
import portal

app = FastAPI(title="SevaAgent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(portal.router)
app.mount("/portal", StaticFiles(directory=Path(__file__).parent / "portal", html=True), name="portal")

BASE_URL = os.getenv("SEVA_BASE_URL", "http://127.0.0.1:8000")


class ParseReq(BaseModel):
    text: str
    lang: str = "en"


class MatchReq(BaseModel):
    profile: dict
    lang: str = "en"


class ApplyReq(BaseModel):
    profile: dict
    scheme_id: str


@app.get("/api/health")
def health():
    return {"groq": llm.has_groq(), "gemini": llm.has_gemini(), "schemes": len(engine.SCHEMES)}


@app.post("/api/parse")
async def parse(req: ParseReq):
    profile, mode = await llm.parse_profile(req.text)
    return {"profile": profile, "mode": mode}


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        return {"ok": False, "error": "File too large (max 8 MB)"}
    res = await llm.extract_from_image(data, file.content_type or "image/jpeg")
    if res is None:
        return {"ok": False, "error": "Document reading is unavailable (set GEMINI_API_KEY) or the call failed. Enter details manually."}
    fields, confidence = res
    return {"ok": True, "fields": fields, "confidence": confidence}


@app.post("/api/match")
async def match(req: MatchReq):
    profile = llm.normalize_profile(req.profile)
    matches = engine.match_schemes(profile)
    summary = await llm.explain(profile, matches, req.lang)
    return {"profile": profile, "matches": matches, "summary": summary, "disclaimer": engine.DISCLAIMER}


@app.post("/api/apply")
async def apply(req: ApplyReq):
    scheme = engine.SCHEMES.get(req.scheme_id)

    async def gen():
        if not scheme or not scheme["agent_supported"]:
            yield f"data: {json.dumps({'type': 'failed', 'reason': 'This scheme is not supported by the agent yet'})}\n\n"
            return
        profile = llm.normalize_profile(req.profile)
        try:
            async for ev in agent.run_agent(profile, scheme["id"], scheme["name"], BASE_URL):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:  # surface browser/setup problems to the UI
            yield f"data: {json.dumps({'type': 'step', 'text': 'Agent error: ' + str(e)[:200], 'status': 'error'})}\n\n"
            yield f"data: {json.dumps({'type': 'failed', 'reason': 'Agent could not run. Is Playwright Chromium installed?'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), lang: str = Form("en")):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        return {"ok": False, "error": "Recording too large"}
    text, err = await llm.transcribe_audio(data, file.filename or "speech.webm", file.content_type or "audio/webm", lang)
    if err:
        return {"ok": False, "error": err}
    if not text:
        return {"ok": False, "error": "No speech detected. Speak closer to the microphone and try again."}
    return {"ok": True, "text": text}
