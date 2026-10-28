"""
Pipelines — data flow automation tied to real connections.

Every pipeline MUST reference a real connection the user owns.
On create/update, we test the connection first — if it doesn't work,
the pipeline is rejected.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.pipeline import Pipeline
from app.models.connection import Connection
from app.core.security import decrypt_credential
from app.models.alert import Alert
from app.models.dashboard import Dashboard
from app.services.cache_service import query_cache

router = APIRouter(prefix="/pipelines", tags=["Pipelines"])


# ---------- SCHEMAS ----------

class PipelineCreate(BaseModel):
    name: str
    connection_id: int
    description: Optional[str] = None
    steps: Optional[str] = None
    schedule: Optional[str] = "manual"
    alert_threshold_pct: Optional[int] = 10
    dashboard_id: Optional[int] = None
    metric_query: Optional[str] = None


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[str] = None
    schedule: Optional[str] = None
    alert_threshold_pct: Optional[int] = None
    is_active: Optional[bool] = None


class PipelineOut(BaseModel):
    id: int
    connection_id: Optional[int]
    connection_name: Optional[str] = None
    name: str
    description: Optional[str]
    source: Optional[str]
    target: Optional[str]
    steps: Optional[str]
    schedule: Optional[str]
    alert_threshold_pct: Optional[int]
    dashboard_id: Optional[int] = None
    dashboard_name: Optional[str] = None
    metric_query: Optional[str] = None
    last_metric_value: Optional[int] = None
    last_metric_at: Optional[datetime] = None
    is_active: bool
    last_run: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    run_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class PipelineTestResponse(BaseModel):
    ok: bool
    message: str


# ---------- HELPERS ----------

def _build_connector(conn: Connection):
    """Instantiate the right connector for a connection — or None."""
    try:
        if conn.db_type == "duckdb":
            from app.services.db_connectors.duckdb_service import DuckDBService
            import os, pandas as pd
            if not conn.file_path or not os.path.exists(conn.file_path):
                return None
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
                return None
            df.columns = [
                "".join(c if c.isalnum() else "_" for c in str(col)).strip("_").lower() or f"col_{i}"
                for i, col in enumerate(df.columns)
            ]
            return DuckDBService(df_map={"data": df})
        if conn.db_type == "access":
            from app.services.db_connectors.access_connector import AccessConnector
            return AccessConnector(file_path=conn.file_path)
        if conn.db_type == "postgres":
            from app.services.db_connectors.postgres_connector import PostgresConnector
            return PostgresConnector(
                host=conn.host, port=conn.port, database_name=conn.database_name,
                username=conn.username, password=decrypt_credential(conn.encrypted_password),
            )
        if conn.db_type == "mysql":
            from app.services.db_connectors.mysql_connector import MySQLConnector
            return MySQLConnector(
                host=conn.host, port=conn.port, database_name=conn.database_name,
                username=conn.username, password=decrypt_credential(conn.encrypted_password),
            )
        if conn.db_type == "snowflake":
            from app.services.db_connectors.snowflake_connector import SnowflakeConnector
            return SnowflakeConnector(
                host=conn.host, port=conn.port, database_name=conn.database_name,
                username=conn.username, password=decrypt_credential(conn.encrypted_password),
            )
        if conn.db_type == "bigquery":
            from app.services.db_connectors.bigquery_connector import BigQueryConnector
            return BigQueryConnector(
                database_name=conn.database_name,
                password=decrypt_credential(conn.encrypted_password) if conn.encrypted_password else None,
            )
        if conn.db_type == "databricks":
            from app.services.db_connectors.databricks_connector import DatabricksConnector
            return DatabricksConnector(
                host=conn.host, port=conn.port, database_name=conn.database_name,
                username=conn.username, password=decrypt_credential(conn.encrypted_password),
            )
        if conn.db_type == "sqlserver":
            from app.services.db_connectors.sqlserver_connector import SQLServerConnector
            return SQLServerConnector(
                host=conn.host, port=conn.port, database_name=conn.database_name,
                username=conn.username, password=decrypt_credential(conn.encrypted_password),
            )
    except Exception as e:
        print(f"[pipeline] connector build failed for {conn.db_type}: {e}")
    return None


def _test_connection(conn: Connection) -> tuple:
    """Returns (ok, message)."""
    connector = _build_connector(conn)
    if not connector:
        return False, "Could not build connector for this data source"
    try:
        if connector.test_connection():
            return True, "Connection OK"
        return False, "Connection test failed"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def _to_out(db: Session, p: Pipeline) -> PipelineOut:
    conn_name = None
    if p.connection_id:
        c = db.query(Connection).filter(Connection.id == p.connection_id).first()
        if c:
            conn_name = c.name

    data = {c.name: getattr(p, c.name) for c in p.__table__.columns}

    # Coerce NULLs to safe defaults (legacy rows may have NULL after migration)
    data["run_count"] = data.get("run_count") or 0
    data["alert_threshold_pct"] = data.get("alert_threshold_pct") or 10
    data["is_active"] = bool(data.get("is_active")) if data.get("is_active") is not None else True
    data["name"] = data.get("name") or "Untitled pipeline"
    data["connection_name"] = conn_name

    # Dashboard name (if linked)
    dash_name = None
    if p.dashboard_id:
        d = db.query(Dashboard).filter(Dashboard.id == p.dashboard_id).first()
        if d:
            dash_name = d.title
    data["dashboard_name"] = dash_name

    # Coerce metric value to int
    lmv = data.get("last_metric_value")
    if lmv is not None:
        try:
            data["last_metric_value"] = int(round(float(lmv)))
        except Exception:
            data["last_metric_value"] = None

    return PipelineOut(**data)


# ---------- ENDPOINTS ----------

@router.get("/available-connections")
def available_connections(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """List connections that pass a live test — for use in the pipeline form."""
    conns = db.query(Connection).filter(Connection.user_id == user.id).all()
    out = []
    for c in conns:
        ok, msg = _test_connection(c)
        out.append({
            "id": c.id,
            "name": c.name,
            "db_type": c.db_type,
            "is_reachable": ok,
            "message": msg,
        })
    return {"connections": out}


@router.get("", response_model=List[PipelineOut])
def list_pipelines(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Pipeline).filter(Pipeline.user_id == user.id)\
        .order_by(Pipeline.created_at.desc()).all()
    return [_to_out(db, p) for p in rows]


@router.post("", response_model=PipelineOut)
def create_pipeline(payload: PipelineCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    if not payload.name.strip():
        raise HTTPException(400, "Pipeline name is required")

    conn = db.query(Connection).filter(
        Connection.id == payload.connection_id,
        Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    ok, msg = _test_connection(conn)
    if not ok:
        raise HTTPException(400, f"Cannot use this connection: {msg}")

    # Validate dashboard if provided
    if payload.dashboard_id:
        dash = db.query(Dashboard).filter(
            Dashboard.id == payload.dashboard_id,
            Dashboard.user_id == user.id,
        ).first()
        if not dash:
            raise HTTPException(404, "Dashboard not found")

    p = Pipeline(
        user_id=user.id,
        connection_id=conn.id,
        name=payload.name.strip(),
        description=payload.description,
        source=conn.name,
        target="dashboard",
        steps=payload.steps,
        schedule=payload.schedule or "manual",
        alert_threshold_pct=payload.alert_threshold_pct or 10,
        dashboard_id=payload.dashboard_id,
        metric_query=payload.metric_query,
        is_active=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(db, p)


@router.get("/{pipeline_id}", response_model=PipelineOut)
def get_pipeline(pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return _to_out(db, p)


@router.patch("/{pipeline_id}", response_model=PipelineOut)
def update_pipeline(pipeline_id: int, payload: PipelineUpdate,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _to_out(db, p)


@router.post("/{pipeline_id}/toggle", response_model=PipelineOut)
def toggle_pipeline(pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    p.is_active = not p.is_active
    db.commit()
    db.refresh(p)
    return _to_out(db, p)


@router.post("/{pipeline_id}/test", response_model=PipelineTestResponse)
def test_pipeline_connection(pipeline_id: int,
                             db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """Re-test the pipeline's connection right now."""
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    if not p.connection_id:
        return PipelineTestResponse(ok=False, message="Pipeline has no connection")
    conn = db.query(Connection).filter(Connection.id == p.connection_id).first()
    if not conn:
        return PipelineTestResponse(ok=False, message="Connection no longer exists")
    ok, msg = _test_connection(conn)
    return PipelineTestResponse(ok=ok, message=msg)


@router.post("/{pipeline_id}/run", response_model=PipelineOut)
def run_pipeline(pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Run a pipeline:
      1. Test connection
      2. Execute metric query (default: row count)
      3. Compare to previous metric
      4. Create alert if change > threshold
      5. Invalidate linked dashboard cache
    """
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    if not p.connection_id:
        raise HTTPException(400, "Pipeline has no connection configured")

    conn = db.query(Connection).filter(Connection.id == p.connection_id).first()
    if not conn:
        raise HTTPException(400, "Connection no longer exists")

    ok, msg = _test_connection(conn)
    p.last_run = datetime.utcnow()
    p.run_count = (p.run_count or 0) + 1

    if not ok:
        p.last_status = "failed"
        p.last_error = msg
        db.commit()
        db.refresh(p)
        raise HTTPException(400, f"Pipeline run failed: {msg}")

    # ---------- Execute metric ----------
    metric_before = p.last_metric_value
    metric_after = None
    metric_error = None

    try:
        connector = _build_connector(conn)
        if connector:
            query = p.metric_query or "SELECT COUNT(*) AS metric FROM data"
            df = connector.execute_query(query)
            if df is not None and len(df) > 0 and df.shape[1] > 0:
                metric_after = int(round(float(df.iloc[0, 0])))
    except Exception as e:
        metric_error = str(e)[:200]
        print(f"[pipeline run] metric query failed: {metric_error}")

    # ---------- Compare + alert ----------
    threshold = p.alert_threshold_pct or 10
    alert_created = None

    if metric_before is not None and metric_after is not None and metric_before != 0:
        change_pct = ((metric_after - metric_before) / abs(metric_before)) * 100.0
        if abs(change_pct) >= threshold:
            direction = "increased" if change_pct > 0 else "decreased"
            severity = "error" if abs(change_pct) >= (threshold * 2) else "warning"
            alert_created = Alert(
                user_id=user.id,
                title=f'Pipeline "{p.name}" {direction} by {abs(change_pct):.1f}%',
                message=f"Metric went from {metric_before:,} to {metric_after:,} (threshold +/-{threshold}%)",
                severity=severity,
            )
            db.add(alert_created)

    # ---------- Update pipeline ----------
    p.last_metric_value = metric_after
    p.last_metric_at = datetime.utcnow()
    p.last_status = "success"
    p.last_error = metric_error
    db.commit()
    db.refresh(p)

    # ---------- Invalidate linked dashboard ----------
    if p.dashboard_id:
        try:
            query_cache.clear()
        except Exception as e:
            print(f"[pipeline run] dashboard cache clear failed: {e}")

    if alert_created:
        db.refresh(alert_created)
        print(f"[pipeline run] alert created: {alert_created.title}")

    return _to_out(db, p)


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    db.delete(p)
    db.commit()
    return {"ok": True}