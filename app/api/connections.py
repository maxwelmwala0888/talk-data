from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
import os, shutil
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.schemas.connection import ConnectionCreate, ConnectionOut, ConnectionTest
from app.core.security import encrypt_credential, decrypt_credential
from app.core.config import settings
from app.services.chains.embed_chain import index_schema
from app.services.cache_service import query_cache

# NOTE: DB connectors are imported lazily inside build_connector() so that
# a missing optional driver (snowflake, databricks, bigquery) doesn't break
# the entire /connections router.

router = APIRouter(prefix="/connections", tags=["Connections"])


def build_connector(db_type: str, config: dict):
    """Lazily import and instantiate the right connector for the given db_type."""
    db_type = (db_type or "").lower()

    if db_type == "postgres":
        from app.services.db_connectors.postgres_connector import PostgresConnector
        return PostgresConnector(**config)

    if db_type == "mysql":
        try:
            from app.services.db_connectors.mysql_connector import MySQLConnector
            return MySQLConnector(**config)
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"MySQL driver missing: run `pip install pymysql`. ({e})")

    if db_type == "sqlserver":
        try:
            from app.services.db_connectors.sqlserver_connector import SQLServerConnector
            return SQLServerConnector(**config)
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"SQL Server driver missing: run `pip install pyodbc`. ({e})")

    if db_type == "snowflake":
        try:
            from app.services.db_connectors.snowflake_connector import SnowflakeConnector
            return SnowflakeConnector(**config)
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Snowflake driver missing: run `pip install snowflake-connector-python`. ({e})")

    if db_type == "bigquery":
        try:
            from app.services.db_connectors.bigquery_connector import BigQueryConnector
            return BigQueryConnector(**config)
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"BigQuery driver missing: run `pip install google-cloud-bigquery`. ({e})")

    if db_type == "databricks":
        try:
            from app.services.db_connectors.databricks_connector import DatabricksConnector
            return DatabricksConnector(**config)
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Databricks driver missing: run `pip install databricks-sql-connector`. ({e})")

    raise HTTPException(status_code=400, detail=f"Unsupported db_type: {db_type}")


def _embed_safely(db, conn, connector):
    try:
        return index_schema(db, conn.id, connector.get_schema())
    except Exception as e:
        print(f"[embed] failed for connection {conn.id}: {e}")
        return 0


@router.get("/supported")
def supported_types():
    return {
        "types": [
            {"value": "postgres",   "label": "PostgreSQL", "defaultPort": 5432},
            {"value": "mysql",      "label": "MySQL",      "defaultPort": 3306},
            {"value": "sqlserver",  "label": "SQL Server", "defaultPort": 1433},
            {"value": "snowflake",  "label": "Snowflake",  "defaultPort": 443},
            {"value": "bigquery",   "label": "BigQuery",   "defaultPort": 443},
            {"value": "databricks", "label": "Databricks", "defaultPort": 443},
        ]
    }


@router.post("/test")
def test_connection(payload: ConnectionTest, user: User = Depends(get_current_user)):
    config = payload.model_dump(exclude={"db_type"})
    if payload.db_type == "databricks":
        config["host"] = (config.get("host") or "").replace("https://", "").replace("http://", "").strip("/")

    try:
        connector = build_connector(payload.db_type, config)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to build connector: {type(e).__name__}: {e}")

    try:
        if connector.test_connection():
            return {"ok": True, "message": f"{payload.db_type} connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {str(e)[:400]}")

    # Expose the driver's last error if available
    last_err = getattr(connector, "_last_error", None)
    if last_err:
        raise HTTPException(status_code=400, detail=f"Connection failed: {last_err}")
    raise HTTPException(status_code=400, detail="Connection test failed (no detailed error available)")


@router.post("", response_model=ConnectionOut)
def create_connection(payload: ConnectionCreate, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    host = payload.host
    if payload.db_type == "databricks" and host:
        host = host.replace("https://", "").replace("http://", "").strip("/")

    conn = Connection(
        user_id=user.id, name=payload.name, db_type=payload.db_type,
        host=host, port=payload.port, database_name=payload.database_name,
        username=payload.username,
        encrypted_password=encrypt_credential(payload.password) if payload.password else None,
    )
    db.add(conn); db.commit(); db.refresh(conn)

    # Best-effort schema indexing
    try:
        connector = build_connector(payload.db_type, {
            "host": host, "port": payload.port, "database_name": payload.database_name,
            "username": payload.username, "password": payload.password,
        })
        _embed_safely(db, conn, connector)
    except Exception as e:
        print(f"[create_connection] embedding skipped: {e}")

    return conn


@router.get("", response_model=list[ConnectionOut])
def list_connections(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Connection).filter(Connection.user_id == user.id).all()


@router.post("/{connection_id}/reindex")
def reindex_connection(connection_id: int, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(Connection.id == connection_id,
                                       Connection.user_id == user.id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    config = {
        "host": conn.host, "port": conn.port, "database_name": conn.database_name,
        "username": conn.username,
        "password": decrypt_credential(conn.encrypted_password) if conn.encrypted_password else None,
    }

    if conn.db_type == "duckdb":
        import pandas as pd
        from app.services.db_connectors.duckdb_service import DuckDBService
        ext = conn.file_path.split(".")[-1].lower()
        if ext == "csv":
            df = pd.read_csv(conn.file_path)
        elif ext in ("xls", "xlsx"):
            df = pd.read_excel(conn.file_path)
        elif ext == "json":
            df = pd.read_json(conn.file_path)
        elif ext == "parquet":
            df = pd.read_parquet(conn.file_path)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file")
        df.columns = [
            "".join(c if c.isalnum() else "_" for c in str(col)).strip("_").lower() or f"col_{i}"
            for i, col in enumerate(df.columns)
        ]
        connector = DuckDBService(df_map={"data": df})
    elif conn.db_type == "access":
        from app.services.db_connectors.access_connector import AccessConnector
        connector = AccessConnector(file_path=conn.file_path)
    else:
        connector = build_connector(conn.db_type, config)

    count = _embed_safely(db, conn, connector)
    query_cache.invalidate_connection(conn.id)
    return {"ok": True, "indexed": count}


@router.delete("/{connection_id}")
def delete_connection(connection_id: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(Connection.id == connection_id,
                                       Connection.user_id == user.id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    query_cache.invalidate_connection(conn.id)
    db.delete(conn); db.commit()
    return {"ok": True}


@router.post("/upload-access", response_model=ConnectionOut)
def upload_access_file(name: str = Form(...), file: UploadFile = File(...),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.db_connectors.access_connector import AccessConnector
    os.makedirs(settings.TEMP_ACCESS_DIR, exist_ok=True)
    file_path = os.path.join(settings.TEMP_ACCESS_DIR, f"{user.id}_{file.filename}")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        connector = AccessConnector(file_path=file_path)
        if not connector.test_connection():
            raise HTTPException(status_code=400, detail="Invalid Access file")
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Failed to read Access file: {e}")

    conn = Connection(user_id=user.id, name=name, db_type="access", file_path=file_path)
    db.add(conn); db.commit(); db.refresh(conn)
    try:
        _embed_safely(db, conn, AccessConnector(file_path=file_path))
    except Exception:
        pass
    return conn