from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import os
import shutil
import pandas as pd

from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.services.data_profiler import profile_dataframe
from app.services.cleaning_planner import plan_cleaning
from app.services.pipeline_executor import execute_pipeline
from app.services import cleaning_history
from app.services import cleaning_history


router = APIRouter(prefix="/datasets", tags=["Cleaner"])


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

    raise HTTPException(400, f"Cleaning not supported for db_type: {conn.db_type}")


# ---------- SCHEMAS ----------

class PlanRequest(BaseModel):
    hint: Optional[str] = ""


class PlanResponse(BaseModel):
    summary: str
    steps: List[Dict[str, Any]]


class ApplyRequest(BaseModel):
    steps: List[Dict[str, Any]]
    save_as_new: bool = False
    replace_original: bool = True
    new_name: Optional[str] = None


class ApplyResponse(BaseModel):
    ok: bool
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    applied: List[Dict[str, Any]]
    new_dataset_id: Optional[int] = None
    new_dataset_name: Optional[str] = None
    health_before: Optional[int] = None
    health_after: Optional[int] = None
    replaced: bool = False


# ---------- ENDPOINTS ----------

@router.post("/{dataset_id}/plan-clean", response_model=PlanResponse)
def plan_clean(dataset_id: int, payload: PlanRequest,
               db: Session = Depends(get_db),
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
    result = plan_cleaning(profile, user_hint=payload.hint or "")
    return PlanResponse(summary=result.get("summary", ""), steps=result.get("steps", []))


@router.post("/{dataset_id}/apply-clean", response_model=ApplyResponse)
def apply_clean(dataset_id: int, payload: ApplyRequest,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")

    df = _load_dataframe(conn)
    rows_before = len(df)
    cols_before = len(df.columns)

    health_before = None
    try:
        s = df.head(5000) if len(df) > 5000 else df
        health_before = profile_dataframe(s).get("health_score")
    except Exception:
        pass

    cleaned_df, applied = execute_pipeline(df, payload.steps)

    rows_after = len(cleaned_df)
    cols_after = len(cleaned_df.columns)

    health_after = None
    try:
        s = cleaned_df.head(5000) if len(cleaned_df) > 5000 else cleaned_df
        health_after = profile_dataframe(s).get("health_score")
    except Exception:
        pass

    new_dataset_id = None
    new_dataset_name = None
    replaced = False

    # ---------- REPLACE ORIGINAL ----------
    if payload.replace_original:
        # Record history BEFORE touching the file
        try:
            cleaning_history.record_clean(
                file_path=conn.file_path,
                steps=payload.steps,
                rows_before=rows_before,
                rows_after=rows_after,
                cols_before=cols_before,
                cols_after=cols_after,
                health_before=health_before,
                health_after=health_after,
                replaced=True,
            )
        except Exception as e:
            print(f"[cleaner] history record failed: {e}")

        # Record history BEFORE touching the file
        try:
            cleaning_history.record_clean(
                file_path=conn.file_path,
                steps=payload.steps,
                rows_before=rows_before,
                rows_after=rows_after,
                cols_before=cols_before,
                cols_after=cols_after,
                health_before=health_before,
                health_after=health_after,
                replaced=True,
            )
        except Exception as e:
            print(f"[cleaner] history record failed: {e}")

        if conn.db_type == "access":
            # Can't write .accdb — save as CSV, re-point Connection
            base = os.path.splitext(conn.file_path)[0]
            cleaned_path = f"{base}_cleaned.csv"
            cleaned_df.to_csv(cleaned_path, index=False)
            conn.file_path = cleaned_path
            conn.db_type = "duckdb"
        else:
            # Write in place via temp file (atomic)
            base, ext = os.path.splitext(conn.file_path)
            ext_l = ext.lower()
            tmp_path = f"{base}.cleaning{ext_l}"
            if ext_l == ".csv":
                cleaned_df.to_csv(tmp_path, index=False)
            elif ext_l in (".xls", ".xlsx"):
                cleaned_df.to_excel(tmp_path, index=False)
            elif ext_l == ".json":
                cleaned_df.to_json(tmp_path, orient="records", indent=2)
            elif ext_l == ".parquet":
                cleaned_df.to_parquet(tmp_path, index=False)
            else:
                cleaned_df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, conn.file_path)

        db.commit()
        db.refresh(conn)
        new_dataset_id = conn.id
        new_dataset_name = conn.name
        replaced = True

    # ---------- SAVE AS NEW ----------
    elif payload.save_as_new:
        base = os.path.splitext(conn.file_path)[0]
        cleaned_path = f"{base}_cleaned.csv"
        cleaned_df.to_csv(cleaned_path, index=False)
        display_name = payload.new_name or f"{conn.name} (cleaned)"
        new_conn = Connection(
            user_id=user.id,
            name=display_name,
            db_type="duckdb",
            file_path=cleaned_path,
            database_name="data",
        )
        db.add(new_conn)
        db.commit()
        db.refresh(new_conn)
        new_dataset_id = new_conn.id
        new_dataset_name = new_conn.name

    return ApplyResponse(
        ok=True,
        rows_before=rows_before,
        rows_after=rows_after,
        columns_before=cols_before,
        columns_after=cols_after,
        applied=applied,
        new_dataset_id=new_dataset_id,
        new_dataset_name=new_dataset_name,
        health_before=health_before,
        health_after=health_after,
        replaced=replaced,
    )


# ---------- SINGLE-STEP PREVIEW ----------

class PreviewRequest(BaseModel):
    step: Dict[str, Any]
    sample_size: int = 8


class PreviewResponse(BaseModel):
    before: List[Dict[str, Any]]
    after: List[Dict[str, Any]]
    affected_rows: int
    total_rows: int
    columns_before: List[str]
    columns_after: List[str]
    error: Optional[str] = None


@router.post("/{dataset_id}/preview-fix", response_model=PreviewResponse)
def preview_fix(dataset_id: int, payload: PreviewRequest,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    df = _load_dataframe(conn)
    total_rows = len(df)
    before_cols = list(df.columns)
    before_sample = df.head(payload.sample_size).astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    before_sample = [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in before_sample]

    try:
        cleaned, _ = execute_pipeline(df, [payload.step])
    except Exception as e:
        return PreviewResponse(
            before=before_sample, after=[],
            affected_rows=0, total_rows=total_rows,
            columns_before=before_cols, columns_after=before_cols,
            error=str(e)[:200],
        )

    after_cols = list(cleaned.columns)
    after_sample = cleaned.head(payload.sample_size).astype(object).where(pd.notnull(cleaned), None).to_dict(orient="records")
    after_sample = [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in after_sample]
    affected_rows = abs(total_rows - len(cleaned))

    return PreviewResponse(
        before=before_sample, after=after_sample,
        affected_rows=affected_rows, total_rows=total_rows,
        columns_before=before_cols, columns_after=after_cols,
    )


# ---------- PROJECTED HEALTH ----------

class ProjectRequest(BaseModel):
    steps: List[Dict[str, Any]]


class ProjectResponse(BaseModel):
    health_before: int
    health_after: int
    rows_before: int
    rows_after: int
    cols_before: int
    cols_after: int
    issues_before: int
    issues_after: int
    duplicates_before: int = 0
    duplicates_after: int = 0
    category_counts_after: Dict[str, int] = {}
    errors: List[str] = []


@router.post("/{dataset_id}/project-health", response_model=ProjectResponse)
def project_health(dataset_id: int, payload: ProjectRequest,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    df = _load_dataframe(conn)
    rows_before = len(df)
    cols_before = len(df.columns)

    sample = df.head(3000) if len(df) > 3000 else df
    prof_before = profile_dataframe(sample)
    issues_before = sum(len(c.get("issues", [])) for c in prof_before.get("columns", []))

    errors: List[str] = []
    try:
        cleaned, _ = execute_pipeline(sample.copy(), payload.steps)
    except Exception as e:
        cleaned = sample
        errors.append(str(e)[:200])

    prof_after = profile_dataframe(cleaned)
    issues_after = sum(len(c.get("issues", [])) for c in prof_after.get("columns", []))

    return ProjectResponse(
        health_before=prof_before.get("health_score", 0),
        health_after=prof_after.get("health_score", 0),
        rows_before=rows_before,
        rows_after=len(cleaned),
        cols_before=cols_before,
        cols_after=len(cleaned.columns),
        issues_before=issues_before,
        issues_after=issues_after,
        duplicates_before=prof_before.get("duplicate_rows", 0),
        duplicates_after=prof_after.get("duplicate_rows", 0),
        category_counts_after=prof_after.get("category_counts", {}),
        errors=errors,
    )

# ---------- CLEANING HISTORY ----------

class HistoryEntry(BaseModel):
    id: str
    timestamp: str
    steps: List[Dict[str, Any]] = []
    step_count: int = 0
    rows_before: int = 0
    rows_after: int = 0
    cols_before: int = 0
    cols_after: int = 0
    health_before: Optional[int] = None
    health_after: Optional[int] = None
    replaced: bool = False
    can_revert: bool = False
    is_revert: bool = False


class HistoryResponse(BaseModel):
    entries: List[HistoryEntry]
    count: int


@router.get("/{dataset_id}/history", response_model=HistoryResponse)
def get_history(dataset_id: int,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    if not conn.file_path:
        return HistoryResponse(entries=[], count=0)

    raw = cleaning_history.load_history(conn.file_path)
    # Sort newest first
    raw.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Force unique IDs — legacy entries may share second-precision timestamps
    seen_ids = set()
    safe = []
    for i, e in enumerate(raw):
        e2 = {k: v for k, v in e.items() if k != "backup_path"}
        eid = str(e2.get("id") or "")
        if not eid or eid in seen_ids:
            # Deterministic unique fallback
            eid = f"{eid or 'entry'}_{i}"
            e2["id"] = eid
        seen_ids.add(eid)
        try:
            safe.append(HistoryEntry(**e2))
        except Exception:
            continue
    return HistoryResponse(entries=safe, count=len(safe))


@router.post("/{dataset_id}/revert/{entry_id}")
def revert_clean(dataset_id: int, entry_id: str,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    if not conn.file_path:
        raise HTTPException(400, "Dataset has no file path")

    result = cleaning_history.revert_to_entry(conn.file_path, entry_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Revert failed"))
    return result


@router.delete("/{dataset_id}/history")
def clear_history_endpoint(dataset_id: int,
                           db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    if not conn.file_path:
        return {"ok": True, "cleared": 0}
    n = cleaning_history.clear_history(conn.file_path)
    return {"ok": True, "cleared": n}
