from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
import os
import math
import pandas as pd
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.services.db_connectors.duckdb_service import DuckDBService

router = APIRouter(prefix="/datasets", tags=["Datasets"])
UPLOAD_DIR = "./data/uploads"
DUCKDB_CACHE_DIR = "./data/duckdb_cache"


def _safe_table_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename or "data"))[0]
    clean = "".join(c if c.isalnum() else "_" for c in base).strip("_").lower()
    if not clean:
        clean = "data"
    if not clean[0].isalpha():
        clean = "t_" + clean
    return clean


def _json_safe(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    return v


def _records_safe(df: pd.DataFrame) -> list:
    return [{col: _json_safe(row[col]) for col in df.columns} for row in df.to_dict(orient="records")]


def _load_file_to_df(file_path: str):
    ext = file_path.split(".")[-1].lower()
    if ext == "csv":
        df = pd.read_csv(file_path)
    elif ext in ("xls", "xlsx"):
        df = pd.read_excel(file_path)
    elif ext == "json":
        df = pd.read_json(file_path)
    elif ext == "parquet":
        df = pd.read_parquet(file_path)
    else:
        raise ValueError(f"Unsupported extension: {ext}")
    df.columns = [
        "".join(c if c.isalnum() else "_" for c in str(col)).strip("_").lower() or f"col_{i}"
        for i, col in enumerate(df.columns)
    ]
    return {_safe_table_name(file_path): df}


@router.post("/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, f"{user.id}_{file.filename}")

    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    # Fast parse — just peek at the first few rows for the response
    try:
        ext = file_path.split(".")[-1].lower()
        if ext == "csv":
            preview_df = pd.read_csv(file_path, nrows=5)
        elif ext in ("xls", "xlsx"):
            preview_df = pd.read_excel(file_path, nrows=5)
        elif ext == "json":
            preview_df = pd.read_json(file_path).head(5)
        elif ext == "parquet":
            preview_df = pd.read_parquet(file_path).head(5)
        else:
            raise HTTPException(400, f"Unsupported: {ext}")

        preview_df.columns = [
            "".join(c if c.isalnum() else "_" for c in str(col)).strip("_").lower() or f"col_{i}"
            for i, col in enumerate(preview_df.columns)
        ]
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(400, f"Failed to parse file: {e}")

    table_name = _safe_table_name(file_path)

    conn = Connection(
        user_id=user.id,
        name=file.filename,
        db_type="duckdb",
        file_path=file_path,
        database_name=table_name,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)

    # Return immediately — indexing happens lazily on first query
    return {
        "dataset_id": conn.id,
        "connection_id": conn.id,
        "filename": file.filename,
        "table_name": table_name,
        "columns": list(preview_df.columns),
        "preview": _records_safe(preview_df),
        "row_count": -1,  # counted on first query
        "schema": [],
    }


@router.get("")
def list_datasets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conns = db.query(Connection).filter(
        Connection.user_id == user.id, Connection.db_type == "duckdb"
    ).order_by(Connection.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "db_type": c.db_type,
            "table_name": c.database_name,
            "file_path": c.file_path,
            "created_at": c.created_at,
        }
        for c in conns
    ]


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == dataset_id, Connection.user_id == user.id, Connection.db_type == "duckdb"
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")
    if conn.file_path and os.path.exists(conn.file_path):
        try:
            os.remove(conn.file_path)
        except Exception:
            pass
    # Also remove the DuckDB cache for this file
    try:
        base = os.path.splitext(os.path.basename(conn.file_path))[0]
        safe = "".join(c if c.isalnum() else "_" for c in base).strip("_").lower()
        if os.path.exists(DUCKDB_CACHE_DIR):
            for f in os.listdir(DUCKDB_CACHE_DIR):
                if f.startswith(safe + "_"):
                    try:
                        os.remove(os.path.join(DUCKDB_CACHE_DIR, f))
                    except Exception:
                        pass
    except Exception:
        pass
    db.delete(conn)
    db.commit()
    return {"ok": True}