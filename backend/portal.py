"""Mock scholarship portal API. The HTML lives in portal/index.html. NOT a real government site."""
import random
import re
import string
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/portal-api")

APPLICATIONS = {}

GENDERS = {"Male", "Female", "Other"}
CATEGORIES = {"SC", "ST", "BC", "OBC", "EBC", "General"}
STATES = {"Telangana", "Andhra Pradesh", "Karnataka", "Maharashtra", "Tamil Nadu", "Kerala", "Other"}


class Submission(BaseModel):
    scheme: str = ""
    full_name: str = ""
    dob: str = ""
    gender: str = ""
    category: str = ""
    state: str = ""
    annual_income: str = ""
    course: str = ""
    declaration: bool = False


@router.post("/submit")
def submit(s: Submission):
    errors = []

    def err(field, msg):
        errors.append({"field": field, "message": msg})

    if len(s.full_name.strip()) < 3:
        err("full_name", "Full name is required (minimum 3 characters)")
    try:
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", s.dob):
            raise ValueError
        datetime.strptime(s.dob, "%d/%m/%Y")
    except ValueError:
        err("dob", "Date of birth must be in DD/MM/YYYY format")
    if s.gender not in GENDERS:
        err("gender", "Select a gender")
    if s.category not in CATEGORIES:
        err("category", "Select a category")
    if s.state not in STATES:
        err("state", "Select a state")
    if not re.fullmatch(r"\d{4,8}", s.annual_income):
        err("annual_income", "Annual income must be digits only (no commas or text)")
    if not s.declaration:
        err("declaration", "You must accept the declaration to submit")

    if errors:
        return {"ok": False, "errors": errors}

    app_id = "SEVA-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    APPLICATIONS[app_id] = {"status": "Submitted - pending verification", "scheme": s.scheme, "name": s.full_name, "at": datetime.now().isoformat(timespec="seconds")}
    return {"ok": True, "application_id": app_id, "status": APPLICATIONS[app_id]["status"]}


@router.get("/status/{app_id}")
def status(app_id: str):
    app = APPLICATIONS.get(app_id)
    return {"found": bool(app), **(app or {})}
