"""Eligibility rule engine + tiny BM25 retriever for citations."""
import json
import math
import re
from collections import Counter
from pathlib import Path

DATA = Path(__file__).parent / "schemes.json"
DOCS_DIR = Path(__file__).parent / "data" / "docs"

_raw = json.loads(DATA.read_text(encoding="utf-8"))
SCHEMES = {s["id"]: s for s in _raw["schemes"]}
DISCLAIMER = _raw["disclaimer"]


def _income_limit(rules, profile):
    limit = rules.get("max_income")
    by_cat = rules.get("max_income_by_category") or {}
    cat = profile.get("category")
    if cat in by_cat:
        return by_cat[cat]
    return limit


def evaluate(profile: dict, scheme: dict) -> dict:
    r = scheme["rules"]
    passed, failed, missing = [], [], []

    def has(k):
        return profile.get(k) not in (None, "")

    if "occupations" in r:
        if not has("occupation"):
            missing.append("occupation")
        elif profile["occupation"] in r["occupations"]:
            passed.append(f"Occupation matches ({profile['occupation'].replace('_', ' ')})")
        else:
            failed.append(f"Needs occupation: {', '.join(o.replace('_', ' ') for o in r['occupations'])}")

    if "states" in r:
        if not has("state"):
            missing.append("state")
        elif profile["state"] in r["states"]:
            passed.append(f"Resident of {profile['state']}")
        else:
            failed.append(f"Only for {', '.join(r['states'])}")

    if "genders" in r:
        if not has("gender"):
            missing.append("gender")
        elif profile["gender"] in r["genders"]:
            passed.append("Gender criterion met")
        else:
            failed.append(f"Only for {', '.join(r['genders'])}")

    if "categories" in r:
        if not has("category"):
            missing.append("category")
        elif profile["category"] in r["categories"]:
            passed.append(f"Category {profile['category']} is eligible")
        else:
            failed.append(f"Eligible categories: {', '.join(r['categories'])}")

    limit = _income_limit(r, profile)
    if limit is not None:
        if not has("annual_income"):
            missing.append("annual_income")
        elif profile["annual_income"] <= limit:
            passed.append(f"Income Rs {profile['annual_income']:,} is within Rs {limit:,}")
        else:
            failed.append(f"Income above the Rs {limit:,} limit")

    age = profile.get("age")
    if "min_age" in r:
        if age is None:
            missing.append("age")
        elif age >= r["min_age"]:
            passed.append(f"Age {age} meets minimum {r['min_age']}")
        else:
            failed.append(f"Minimum age is {r['min_age']}")
    if "max_age" in r:
        if age is None:
            missing.append("age")
        elif age <= r["max_age"]:
            passed.append(f"Age {age} within maximum {r['max_age']}")
        else:
            failed.append(f"Maximum age is {r['max_age']}")

    if failed:
        status = "not_eligible"
    elif missing:
        status = "likely"
    else:
        status = "eligible"
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "missing": sorted(set(missing)),
    }


# ---------- BM25 retrieval over scheme fact sheets (+ optional .txt/.md in data/docs) ----------

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are", "with", "i", "my", "am"}


def _tok(text):
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def _build_chunks():
    chunks = []
    for s in SCHEMES.values():
        chunks.append((s["id"], s["name"], "Overview", f"{s['name']}. {s['summary']}"))
        chunks.append((s["id"], s["name"], "Benefits", f"{s['name']} benefits: {s['benefits']}"))
        chunks.append((s["id"], s["name"], "Eligibility", f"{s['name']} eligibility: {s['eligibility_text']}"))
        chunks.append((s["id"], s["name"], "Documents", f"{s['name']} documents: {', '.join(s['documents'])}"))
    if DOCS_DIR.exists():
        for p in sorted(DOCS_DIR.glob("*")):
            if p.suffix.lower() in {".txt", ".md"}:
                text = p.read_text(encoding="utf-8", errors="ignore")
                for i in range(0, len(text), 700):
                    piece = text[i : i + 700].strip()
                    if piece:
                        chunks.append(("doc:" + p.stem, p.stem, f"Document excerpt {i // 700 + 1}", piece))
    return chunks


class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [_tok(c[3]) for c in chunks]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query, k=3, only_ids=None):
        q = _tok(query)
        scored = []
        for i, d in enumerate(self.docs):
            if only_ids and self.chunks[i][0] not in only_ids:
                continue
            tf = Counter(d)
            score = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    score += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        out = []
        for score, i in scored[:k]:
            sid, name, section, text = self.chunks[i]
            out.append({"scheme_id": sid, "scheme_name": name, "section": section, "text": text, "score": round(score, 2)})
        return out


INDEX = BM25(_build_chunks())


def match_schemes(profile: dict):
    results = []
    for s in SCHEMES.values():
        ev = evaluate(profile, s)
        if ev["status"] == "not_eligible":
            continue
        query = " ".join(str(v) for v in profile.values()) + " " + s["name"]
        cites = INDEX.search(query, k=2, only_ids={s["id"]})
        results.append(
            {
                "id": s["id"],
                "name": s["name"],
                "level": s["level"],
                "summary": s["summary"],
                "benefits": s["benefits"],
                "documents": s["documents"],
                "apply_url": s["apply_url"],
                "agent_supported": s["agent_supported"],
                "citations": cites,
                **ev,
            }
        )
    order = {"eligible": 0, "likely": 1}
    results.sort(key=lambda r: (order[r["status"]], not r["agent_supported"], r["name"]))
    return results
