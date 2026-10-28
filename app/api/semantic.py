from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from sqlalchemy.orm import Session
import os
import pandas as pd

from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.services.semantic_cleaner import analyze_column


router = APIRouter(prefix="/datasets", tags=["Semantic"])


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

    raise HTTPException(400, f"Semantic cleaning not supported for db_type: {conn.db_type}")


class AnalyzeRequest(BaseModel):
    column: str
    hint: Optional[str] = ""


class AnalyzeResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    mapping: Dict[str, str] = {}
    unique_count: int = 0
    sampled_count: int = 0
    change_count: int = 0


@router.post("/{dataset_id}/analyze-semantic", response_model=AnalyzeResponse)
def analyze_semantic(
    dataset_id: int,
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id,
        Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")

    df = _load_dataframe(conn)
    result = analyze_column(df, payload.column, hint=payload.hint or "")
    return AnalyzeResponse(**result)


@router.get("/{dataset_id}/column-values")
def get_column_values(dataset_id: int, column: str, limit: int = 30,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Return the top N unique values in a column with their frequencies."""
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    df = _load_dataframe(conn)
    if column not in df.columns:
        raise HTTPException(404, f"Column '{column}' not found")
    counts = df[column].dropna().astype(str).value_counts().head(limit)
    return {
        "column": column,
        "values": [{"value": str(k), "count": int(v)} for k, v in counts.items()],
        "unique_total": int(df[column].nunique(dropna=True)),
    }