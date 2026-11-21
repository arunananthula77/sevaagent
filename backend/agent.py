"""Browser agent: reads a live form, plans the fill (LLM or rules), submits, reads validation errors, recovers."""
import asyncio
import base64
import json
import os
import re

from playwright.async_api import async_playwright

import llm

EXTRACT_JS = """() => {
  const out = [];
  document.querySelectorAll('form input, form select, form textarea').forEach(el => {
    if (el.type === 'hidden' || el.type === 'submit') return;
    let label = '';
    if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) label = l.innerText; }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    out.push({
      name: el.name || el.id, tag: el.tagName.toLowerCase(), type: el.type || '',
      label: label.trim(), placeholder: el.placeholder || '', required: el.required,
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text).filter(t => !t.startsWith('--')) : []
    });
  });
  return out;
}"""

ERRORS_JS = """() => Array.from(document.querySelectorAll('#errors li')).map(li => ({field: li.dataset.field || '', message: li.innerText}))"""


def step(text, status="info"):
    return {"type": "step", "text": text, "status": status}


async def shot(page):
    img = await page.screenshot(type="jpeg", quality=55)
    return {"type": "shot", "image": base64.b64encode(img).decode()}


def pick_option(options, value):
    v = str(value).strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    for o in options:
        if v and (v in o.lower() or o.lower() in v):
            return o
    return None


def heuristic_plan(profile, fields):
    values = {}
    for f in fields:
        if f["type"] == "checkbox":
            continue
        key = f"{f['name']} {f['label']}".lower()
        val = None
        if "birth" in key or "dob" in key:
            val = profile.get("dob")
        elif "gender" in key:
            val = profile.get("gender")
        elif "categ" in key or "caste" in key:
            val = profile.get("category")
        elif "state" in key:
            val = profile.get("state")
        elif "income" in key:
            val = profile.get("annual_income")
        elif "course" in key:
            val = profile.get("course")
        elif "institut" in key or "college" in key:
            val = profile.get("institution")
        elif "name" in key:
            val = profile.get("name")
        if val not in (None, ""):
            values[f["name"]] = str(val)
    return {"values": values, "checkboxes": []}


async def plan_fill(profile, fields):
    if llm.has_groq():
        system = (
            "You fill an online government form for a citizen. Given the citizen profile and the form fields, return JSON "
            '{"values": {field_name: value}, "checkboxes": [field_names to tick]}. For select fields use one of the listed options exactly. '
            "Only use facts in the profile. Skip fields you have no data for. Only tick a declaration checkbox if the profile contains data for the form."
        )
        out = await llm.groq_chat(system, json.dumps({"profile": profile, "fields": fields}, ensure_ascii=False), json_mode=True, max_tokens=500)
        if isinstance(out, dict) and isinstance(out.get("values"), dict) and out["values"]:
            return {"values": {k: str(v) for k, v in out["values"].items()}, "checkboxes": list(out.get("checkboxes") or [])}, "llm"
    return heuristic_plan(profile, fields), "rules"


def to_ddmmyyyy(s):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else s


async def fix_plan(profile, fields, plan, errors):
    if llm.has_groq():
        system = (
            "A web form rejected a submission. Given the profile, fields, the values used and the validation errors, "
            'return corrected JSON {"values": {...full corrected values...}, "checkboxes": [names to tick]}. Only use facts from the profile.'
        )
        payload = {"profile": profile, "fields": fields, "used": plan, "errors": errors}
        out = await llm.groq_chat(system, json.dumps(payload, ensure_ascii=False), json_mode=True, max_tokens=500)
        if isinstance(out, dict) and isinstance(out.get("values"), dict) and out["values"]:
            return {"values": {k: str(v) for k, v in out["values"].items()}, "checkboxes": list(out.get("checkboxes") or [])}, "llm"
    values = dict(plan["values"])
    boxes = list(plan["checkboxes"])
    for e in errors:
        msg, field = e["message"].lower(), e["field"]
        if "dd/mm/yyyy" in msg and field in values:
            values[field] = to_ddmmyyyy(values[field])
        elif ("digits" in msg or "numeric" in msg) and field in values:
            values[field] = re.sub(r"\D", "", values[field])
        elif any(w in msg for w in ("declaration", "accept", "agree")):
            if field and field not in boxes:
                boxes.append(field)
    return {"values": values, "checkboxes": boxes}, "rules"


async def fill(page, fields, plan, prev=None):
    by_name = {f["name"]: f for f in fields}
    for name, value in plan["values"].items():
        f = by_name.get(name)
        if not f or (prev and prev["values"].get(name) == value):
            continue
        sel = f'[name="{name}"]'
        if f["tag"] == "select":
            opt = pick_option(f["options"], value)
            if not opt:
                continue
            await page.select_option(sel, label=opt)
            shown = opt
        else:
            await page.fill(sel, value)
            shown = value
        yield step(f"Filled {f['label'].replace('*', '').strip() or name}: {shown}", "ok")
        await asyncio.sleep(0.25)
    for name in plan["checkboxes"]:
        if name in by_name and not (prev and name in prev["checkboxes"]):
            await page.check(f'[name="{name}"]')
            yield step(f"Ticked {by_name[name]['label'].replace('*', '').strip()[:60]}", "ok")


async def run_agent(profile, scheme_id, scheme_name, base_url):
    yield step("Launching browser agent", "info")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=os.getenv("SEVA_CHROME_PATH") or None, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 760})
            yield step(f"Opening application portal for {scheme_name}")
            await page.goto(f"{base_url}/portal/?scheme={scheme_id}")
            yield await shot(page)

            fields = await page.evaluate(EXTRACT_JS)
            yield step(f"Read the form: found {len(fields)} fields", "ok")

            plan, mode = await plan_fill(profile, fields)
            yield step(f"Planned how to fill the form using {'the LLM' if mode == 'llm' else 'rule-based matching'}", "info")

            prev = None
            for attempt in range(1, 4):
                async for ev in fill(page, fields, plan, prev):
                    yield ev
                prev = plan
                yield await shot(page)

                yield step("Submitting the application" if attempt == 1 else f"Re-submitting (attempt {attempt})")
                await page.click("#submit-btn")
                try:
                    await page.wait_for_selector("#receipt-id, #errors li", timeout=10000)
                except Exception:
                    yield step("The portal did not respond in time", "error")
                    yield {"type": "failed", "reason": "Portal timed out"}
                    return
                yield await shot(page)

                if await page.query_selector("#receipt-id"):
                    receipt = (await page.inner_text("#receipt-id")).strip()
                    yield step(f"Application accepted. Receipt number {receipt}", "ok")
                    yield {"type": "done", "receipt": receipt, "scheme": scheme_name}
                    return

                errors = await page.evaluate(ERRORS_JS)
                for e in errors:
                    yield step(f"Portal rejected: {e['message']}", "warn")
                if attempt == 3:
                    break
                yield step("Reading the errors and correcting the form", "info")
                plan, mode = await fix_plan(profile, fields, plan, errors)
                yield step(f"Fix planned using {'the LLM' if mode == 'llm' else 'rule-based recovery'}", "ok")

            yield step("Could not complete the form automatically. Please finish it manually.", "error")
            yield {"type": "failed", "reason": "Validation errors remained after 3 attempts"}
        finally:
            await browser.close()
