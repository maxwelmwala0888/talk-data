from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import os
import pandas as pd

from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.services.data_profiler import profile_dataframe

router = APIRouter(prefix="/datasets", tags=["Profile"])


def _load_dataframe(conn: Connection) -> pd.DataFrame:
    """Load a dataset as a DataFrame, based on its type."""
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

    raise HTTPException(400, f"Profiling not supported for db_type: {conn.db_type}")


@router.get("/{dataset_id}/profile")
def get_profile(dataset_id: int, sample_limit: int = 5000,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Return a health profile of the dataset — nulls, duplicates, issues, score."""
    conn = db.query(Connection).filter(
        Connection.id == dataset_id,
        Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Dataset not found")

    try:
        df = _load_dataframe(conn)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Failed to load dataset: {str(e)[:200]}")

    # For huge files, sample down to keep profiling fast
    if len(df) > sample_limit:
        df = df.sample(n=sample_limit, random_state=42).reset_index(drop=True)
        sampled = True
    else:
        sampled = False

    profile = profile_dataframe(df)
    profile["sampled"] = sampled
    profile["sample_size"] = len(df)
    profile["connection_name"] = conn.name
    profile["connection_id"] = conn.id
    return profile