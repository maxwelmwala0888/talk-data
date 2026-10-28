"""
Cross-column rules API.

Endpoints:
  GET    /rules                    — list user's rules (optionally filtered by dataset)
  POST   /rules                    — create a rule
  PATCH  /rules/{id}               — update a rule
  DELETE /rules/{id}               — delete a rule
  POST   /rules/suggest            — AI proposes rules for a dataset
  POST   /rules/validate/{dataset_id}  — run all rules against a dataset
  POST   /rules/validate-one       — run a single rule against a dataset
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime
import os
import pandas as pd

from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.models.data_rule import DataRule
from app.services.data_profiler import profile_dataframe
from app.services.rules_engine import validate_expression, evaluate_rule
from app.services.rules_planner import plan_rules


router = APIRouter(prefix="/rules", tags=["Rules"])


# ---------- LOADER ----------

def _load_dataframe(conn: Connection) -> pd.DataFrame:
    if conn.db_type == "duckdb":
        if not conn.file_path or not os.path.exists(conn.file_path):
            raise HTTPException(404, "Dataset file not found")
        ext = conn.file_path.split(".")[-1].lower()
        if ext == "csv":
            return pd.read_csv(conn.file_path)
        elif ext in ("xls", "xlsx"):
            return pd.read_excel(conn.file_path)
        elif ext == "json":
            return pd.read_json(conn.file_path)
        elif ext == "parquet":
            return pd.read_parquet(conn.file_path)
        else:
            raise HTTPException(400, f"Unsupported file: {ext}")
    if conn.db_type == "access":
        if not conn.file_path or not os.path.exists(conn.file_path):
            raise HTTPException(404, "Access file not found")
        from app.services.db_connectors.access_connector import AccessConnector
        acc = AccessConnector(file_path=conn.file_path)
        schema = acc.get_schema()
        if not schema:
            raise HTTPException(400, "Access file has no tables")
        table = schema[0]["table_name"]
        return acc.execute_query(f'SELECT * FROM "{table}"')
    raise HTTPException(400, f"Rules not supported for db_type: {conn.db_type}")


# ---------- SCHEMAS ----------

class RuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    check: str
    severity: str = "warning"
    message: Optional[str] = None
    suggested_fix: Optional[str] = None
    dataset_id: Optional[int] = None
    is_ai_generated: bool = False


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    check: Optional[str] = None
    severity: Optional[str] = None
    message: Optional[str] = None
    suggested_fix: Optional[str] = None
    is_active: Optional[bool] = None


class RuleOut(BaseModel):
    id: int
    dataset_id: Optional[int]
    name: str
    description: Optional[str]
    check: str
    severity: str
    message: Optional[str]
    suggested_fix: Optional[str]
    is_active: bool
    is_ai_generated: bool
    columns_used: Optional[List[str]] = []
    last_violation_count: int
    last_validated_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class SuggestResponse(BaseModel):
    suggested: List[Dict[str, Any]]
    count: int


class ValidateOneRequest(BaseModel):
    dataset_id: int
    check: str


class RuleResult(BaseModel):
    rule_id: Optional[int] = None
    rule_name: str
    ok: bool
    error: Optional[str] = None
    violations: int = 0
    sample_violations: List[Dict[str, Any]] = []
    columns_used: List[str] = []


class ValidateResponse(BaseModel):
    results: List[RuleResult]
    total_violations: int
    rules_run: int


# ---------- HELPERS ----------

def _rule_to_dict(r: DataRule) -> Dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "check": r.check,
        "severity": r.severity,
        "message": r.message,
        "suggested_fix": r.suggested_fix,
        "columns_used": r.columns_used or [],
    }


# ---------- CRUD ----------

@router.get("", response_model=List[RuleOut])
def list_rules(dataset_id: Optional[int] = None,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    q = db.query(DataRule).filter(DataRule.user_id == user.id)
    if dataset_id is not None:
        q = q.filter((DataRule.dataset_id == dataset_id) | (DataRule.dataset_id.is_(None)))
    return q.order_by(DataRule.created_at.desc()).all()


@router.post("", response_model=RuleOut)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if not payload.name.strip():
        raise HTTPException(400, "Name is required")
    if not payload.check.strip():
        raise HTTPException(400, "Check expression is required")

    # Validate against dataset columns if dataset_id given
    cols_used: List[str] = []
    if payload.dataset_id:
        conn = db.query(Connection).filter(
            Connection.id == payload.dataset_id, Connection.user_id == user.id,
        ).first()
        if not conn:
            raise HTTPException(404, "Dataset not found")
        df = _load_dataframe(conn).head(50)
        v = validate_expression(payload.check, list(df.columns))
        if not v["ok"]:
            raise HTTPException(400, v["error"] or "Invalid expression")
        cols_used = v["columns_used"]

    r = DataRule(
        user_id=user.id,
        dataset_id=payload.dataset_id,
        name=payload.name.strip(),
        description=payload.description,
        check=payload.check.strip(),
        severity=payload.severity,
        message=payload.message,
        suggested_fix=payload.suggested_fix,
        is_ai_generated=payload.is_ai_generated,
        columns_used=cols_used,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, payload: RuleUpdate,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(DataRule).filter(
        DataRule.id == rule_id, DataRule.user_id == user.id,
    ).first()
    if not r:
        raise HTTPException(404, "Rule not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return r


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(DataRule).filter(
        DataRule.id == rule_id, DataRule.user_id == user.id,
    ).first()
    if not r:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------- SUGGEST ----------

@router.post("/suggest/{dataset_id}", response_model=SuggestResponse)
def suggest_rules(dataset_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")

    df = _load_dataframe(conn)
    if len(df) > 5000:
        df = df.sample(n=5000, random_state=42).reset_index(drop=True)

    profile = profile_dataframe(df)

    # Don't suggest rules that reference columns we can't validate
    existing = db.query(DataRule).filter(
        DataRule.user_id == user.id,
        (DataRule.dataset_id == dataset_id) | (DataRule.dataset_id.is_(None)),
    ).all()
    existing_checks = {r.check.strip() for r in existing}

    raw = plan_rules(profile, max_rules=8)

    # Filter out duplicates + already-known rules + validate each
    out = []
    cols = list(df.columns)
    for r in raw:
        if r["check"] in existing_checks:
            continue
        v = validate_expression(r["check"], cols)
        if not v["ok"]:
            continue
        r["columns_used"] = v["columns_used"]
        out.append(r)

    return SuggestResponse(suggested=out, count=len(out))


# ---------- VALIDATE ----------

@router.post("/validate/{dataset_id}", response_model=ValidateResponse)
def validate_all(dataset_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")

    df = _load_dataframe(conn)

    rules = db.query(DataRule).filter(
        DataRule.user_id == user.id,
        DataRule.is_active == True,  # noqa: E712
        (DataRule.dataset_id == dataset_id) | (DataRule.dataset_id.is_(None)),
    ).all()

    results: List[RuleResult] = []
    total_violations = 0

    for r in rules:
        res = evaluate_rule(df, _rule_to_dict(r))
        # Persist last_violation_count
        r.last_violation_count = res["violations"]
        r.last_validated_at = datetime.utcnow()
        total_violations += res["violations"]

        results.append(RuleResult(
            rule_id=r.id,
            rule_name=r.name,
            ok=res["ok"],
            error=res.get("error"),
            violations=res["violations"],
            sample_violations=res.get("sample_violations", []),
            columns_used=res.get("columns_used", []),
        ))

    db.commit()
    return ValidateResponse(
        results=results,
        total_violations=total_violations,
        rules_run=len(results),
    )


@router.post("/validate-one", response_model=RuleResult)
def validate_one(payload: ValidateOneRequest, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == payload.dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    df = _load_dataframe(conn)
    res = evaluate_rule(df, {"check": payload.check})
    return RuleResult(
        rule_name="Ad-hoc",
        ok=res["ok"],
        error=res.get("error"),
        violations=res["violations"],
        sample_violations=res.get("sample_violations", []),
        columns_used=res.get("columns_used", []),
    )