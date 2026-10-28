from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Any, Dict, Optional
import os
import pandas as pd
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.dashboard import Dashboard
from app.models.widget import Widget
from app.models.connection import Connection
from app.schemas.dashboard import DashboardCreate, DashboardUpdate, DashboardOut, DashboardDetail
from app.schemas.widget import WidgetCreate, WidgetUpdate, WidgetOut
from app.core.security import decrypt_credential
from app.services.chains.text_to_sql_chain import is_safe_sql
from app.services.chains.insight_chain import generate_chart_and_insights
from app.services.cache_service import query_cache

router = APIRouter(prefix="/dashboards", tags=["Dashboards"])


# ============================================================
#  HELPERS
# ============================================================

def _read_dataframe(file_path):
    ext = file_path.split(".")[-1].lower()
    if ext == "csv": df = pd.read_csv(file_path)
    elif ext in ("xls", "xlsx"): df = pd.read_excel(file_path)
    elif ext == "json": df = pd.read_json(file_path)
    elif ext == "parquet": df = pd.read_parquet(file_path)
    else: raise HTTPException(400, f"Unsupported: {ext}")
    df.columns = ["".join(c if c.isalnum() else "_" for c in str(col)).strip("_").lower() or f"col_{i}"
                  for i, col in enumerate(df.columns)]
    return df


def _clean_rows(df):
    import math
    rows = df.head(2000).astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    out = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if v is None: clean[k] = None
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)): clean[k] = None
            elif hasattr(v, "isoformat"): clean[k] = str(v)
            else: clean[k] = v
        out.append(clean)
    return out


def _duckdb_path_for(file_path):
    try: mtime = int(os.path.getmtime(file_path))
    except OSError: mtime = 0
    duck_dir = os.path.join(".", "data", "duckdb_cache")
    os.makedirs(duck_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(file_path))[0]
    safe = "".join(c if c.isalnum() else "_" for c in base).strip("_").lower() or "data"
    return os.path.join(duck_dir, f"{safe}_{mtime}.duckdb")


def _load_duckdb(conn):
    if not conn.file_path or not os.path.exists(conn.file_path):
        raise HTTPException(404, "Dataset file not found")
    from app.services.db_connectors.duckdb_service import DuckDBService
    db_path = _duckdb_path_for(conn.file_path)
    if os.path.exists(db_path):
        try:
            svc = DuckDBService(db_path=db_path)
            tables = [t[0] for t in svc.conn.execute("SHOW TABLES").fetchall()]
            if "data" in tables:
                return svc
        except Exception as e:
            print(f"[duckdb] failed to open {db_path}: {e}, rebuilding")
    print(f"[duckdb] building {db_path}")
    df = _read_dataframe(conn.file_path)
    DuckDBService(df_map={"data": df}, db_path=db_path)
    return DuckDBService(db_path=db_path)


def _get_connector_for_conn(conn):
    if conn.db_type == "duckdb":
        return _load_duckdb(conn)
    if conn.db_type == "access":
        from app.services.db_connectors.access_connector import AccessConnector
        return AccessConnector(file_path=conn.file_path)
    if conn.db_type == "postgres":
        from app.services.db_connectors.postgres_connector import PostgresConnector
        return PostgresConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                 username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "mysql":
        from app.services.db_connectors.mysql_connector import MySQLConnector
        return MySQLConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                              username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "sqlserver":
        from app.services.db_connectors.sqlserver_connector import SQLServerConnector
        return SQLServerConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                  username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "snowflake":
        from app.services.db_connectors.snowflake_connector import SnowflakeConnector
        return SnowflakeConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                  username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "bigquery":
        from app.services.db_connectors.bigquery_connector import BigQueryConnector
        return BigQueryConnector(database_name=conn.database_name,
                                 password=decrypt_credential(conn.encrypted_password) if conn.encrypted_password else None)
    if conn.db_type == "databricks":
        from app.services.db_connectors.databricks_connector import DatabricksConnector
        return DatabricksConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                   username=conn.username, password=decrypt_credential(conn.encrypted_password))
    raise HTTPException(400, f"Unsupported: {conn.db_type}")


# ============================================================
#  ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ SAVE-FROM-QUERY ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â registered FIRST to avoid /{dashboard_id} conflict
# ============================================================

class SaveQueryPayload(BaseModel):
    connection_id: int
    prompt: str
    sql_query: str
    chart_config: Optional[Dict[str, Any]] = None
    title: Optional[str] = None


@router.post("/save-from-query")
def save_from_query(payload: SaveQueryPayload,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(
        Connection.id == payload.connection_id,
        Connection.user_id == user.id,
    ).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    from app.services.auto_dashboard import attach_widget, get_or_create_auto_dashboard

    widget_title = payload.title or payload.prompt[:60]
    w = attach_widget(
        db=db, user=user, conn=conn,
        prompt=payload.prompt, sql=payload.sql_query,
        chart_config=payload.chart_config or {"chart_type": "table"},
        title=widget_title,
    )
    d = get_or_create_auto_dashboard(db, user, conn)
    return {
        "ok": True,
        "dashboard_id": d.id,
        "dashboard_title": d.title,
        "widget_id": w.id,
        "widget_title": w.title,
    }


# ============================================================
#  DASHBOARDS
# ============================================================

@router.post("", response_model=DashboardOut)
def create_dashboard(payload: DashboardCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = Dashboard(user_id=user.id, title=payload.title, description=payload.description,
                  tags=payload.tags or [], refresh_interval=payload.refresh_interval or 0)
    db.add(d); db.commit(); db.refresh(d)
    return _to_out(d, db, 0)


@router.get("", response_model=list[DashboardOut])
def list_dashboards(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    valid = {c.id for c in db.query(Connection).filter(Connection.user_id == user.id).all()}
    ds = db.query(Dashboard).filter(Dashboard.user_id == user.id)\
        .order_by(Dashboard.updated_at.desc().nullslast(), Dashboard.created_at.desc()).all()
    ds = [d for d in ds if not d.auto_for_connection_id or d.auto_for_connection_id in valid]
    counts = dict(db.query(Widget.dashboard_id, func.count(Widget.id))
                  .filter(Widget.user_id == user.id).group_by(Widget.dashboard_id).all())
    return [_to_out(d, db, counts.get(d.id, 0)) for d in ds]


@router.get("/for-connection/{connection_id}", response_model=DashboardDetail)
def get_auto_dashboard(connection_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conn = db.query(Connection).filter(Connection.id == connection_id, Connection.user_id == user.id).first()
    if not conn: raise HTTPException(404, "Connection not found")
    from app.services.auto_dashboard import get_or_create_auto_dashboard
    d = get_or_create_auto_dashboard(db, user, conn)
    ws = db.query(Widget).filter(Widget.dashboard_id == d.id).order_by(Widget.position_row, Widget.position_col).all()
    return _to_detail(d, ws)


@router.get("/{dashboard_id}", response_model=DashboardDetail)
def get_dashboard(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
    if not d: raise HTTPException(404, "Dashboard not found")
    ws = db.query(Widget).filter(Widget.dashboard_id == d.id).order_by(Widget.position_row, Widget.position_col).all()
    return _to_detail(d, ws)


@router.patch("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: int, payload: DashboardUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
    if not d: raise HTTPException(404, "Dashboard not found")
    for k, v in payload.model_dump(exclude_unset=True).items(): setattr(d, k, v)
    db.commit(); db.refresh(d)
    count = db.query(func.count(Widget.id)).filter(Widget.dashboard_id == d.id).scalar()
    return _to_out(d, db, count or 0)


@router.delete("/{dashboard_id}")
def delete_dashboard(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
    if not d: raise HTTPException(404, "Dashboard not found")
    db.delete(d); db.commit()
    return {"ok": True}


@router.post("/{dashboard_id}/widgets", response_model=WidgetOut)
def create_widget(dashboard_id: int, payload: WidgetCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
    if not d: raise HTTPException(404, "Dashboard not found")
    w = Widget(dashboard_id=d.id, user_id=user.id, connection_id=payload.connection_id,
               title=payload.title, prompt=payload.prompt, sql_query=payload.sql_query,
               chart_config=payload.chart_config, widget_type=payload.widget_type or "chart",
               position_row=payload.position_row, position_col=payload.position_col,
               width=payload.width, height=payload.height)
    db.add(w); db.commit(); db.refresh(w)
    return w


@router.patch("/{dashboard_id}/widgets/{widget_id}", response_model=WidgetOut)
def update_widget(dashboard_id: int, widget_id: int, payload: WidgetUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    w = db.query(Widget).filter(Widget.id == widget_id, Widget.dashboard_id == dashboard_id, Widget.user_id == user.id).first()
    if not w: raise HTTPException(404, "Widget not found")
    for k, v in payload.model_dump(exclude_unset=True).items(): setattr(w, k, v)
    db.commit(); db.refresh(w)
    return w


@router.delete("/{dashboard_id}/widgets/{widget_id}")
def delete_widget(dashboard_id: int, widget_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    w = db.query(Widget).filter(Widget.id == widget_id, Widget.dashboard_id == dashboard_id, Widget.user_id == user.id).first()
    if not w: raise HTTPException(404, "Widget not found")
    db.delete(w); db.commit()
    return {"ok": True}


@router.post("/{dashboard_id}/widgets/{widget_id}/run")
def run_widget(dashboard_id: int, widget_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    w = db.query(Widget).filter(Widget.id == widget_id, Widget.dashboard_id == dashboard_id, Widget.user_id == user.id).first()
    if not w: raise HTTPException(404, "Widget not found")
    if not w.connection_id or not w.sql_query: raise HTTPException(400, "Widget missing config")
    if not is_safe_sql(w.sql_query): raise HTTPException(400, "Not read-only")
    conn = db.query(Connection).filter(Connection.id == w.connection_id, Connection.user_id == user.id).first()
    if not conn: raise HTTPException(404, "Connection not found")
    connector = _get_connector_for_conn(conn)

    try:
        df = connector.execute_query(w.sql_query)
    except Exception as e:
        err = str(e)[:200]
        print(f"[run_widget] widget #{w.id} SQL failed: {err}")
        raise HTTPException(400, f"Query failed: {err}")

    # Self-heal: regenerate SQL from the prompt
    if df is None:
        new_sql = _self_heal_widget(db, w, connector, conn)
        if new_sql:
            try:
                df = connector.execute_query(new_sql)
                err = None
            except Exception as e2:
                err = str(e2)

    if df is None:
        raise HTTPException(400, f"Query failed: {err}")

    return {"widget_id": w.id, "columns": df.columns.tolist(), "rows": _clean_rows(df),
            "row_count": len(df), "chart_config": w.chart_config}


@router.post("/{dashboard_id}/run-all")
def run_all_widgets(dashboard_id: int, force: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
    if not d: raise HTTPException(404, "Dashboard not found")
    widgets = db.query(Widget).filter(Widget.dashboard_id == d.id).all()
    if not widgets: return {"dashboard_id": d.id, "widgets": {}}

    results = {}
    by_conn = {}
    for w in widgets:
        if not w.connection_id: results[w.id] = {"error": "No connection"}
        elif not w.sql_query: results[w.id] = {"error": "No SQL"}
        elif not is_safe_sql(w.sql_query): results[w.id] = {"error": "Not read-only"}
        else: by_conn.setdefault(w.connection_id, []).append(w)

    for conn_id, ws in by_conn.items():
        conn = db.query(Connection).filter(Connection.id == conn_id, Connection.user_id == user.id).first()
        if not conn:
            for w in ws: results[w.id] = {"error": "Connection not found"}
            continue
        try:
            connector = _get_connector_for_conn(conn)
        except Exception as e:
            for w in ws: results[w.id] = {"error": f"Connector: {str(e)[:150]}"}
            continue
        for w in ws:
            ck = ("widget", w.id, w.sql_query, conn.file_path or f"db:{conn.id}")
            if not force:
                cached = query_cache.get(*ck)
                if cached is not None:
                    results[w.id] = cached
                    continue
            try:
                df = connector.execute_query(w.sql_query)
                payload = {"widget_id": w.id, "columns": df.columns.tolist(),
                           "rows": _clean_rows(df), "row_count": len(df),
                           "chart_config": w.chart_config}
                query_cache.set(payload, *ck)
                results[w.id] = payload
            except Exception as e:
                err = str(e)
                results[w.id] = {"error": str(e)[:200]}

    return {"dashboard_id": d.id, "widgets": results}


@router.post("/{dashboard_id}/refresh")
def refresh_dashboard(dashboard_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return run_all_widgets(dashboard_id=dashboard_id, force=True, db=db, user=user)


def _to_out(d, db, count=None):
    if count is None:
        count = db.query(func.count(Widget.id)).filter(Widget.dashboard_id == d.id).scalar() or 0
    return DashboardOut(id=d.id, title=d.title, description=d.description, tags=d.tags or [],
                        refresh_interval=d.refresh_interval or 0, is_favorite=bool(d.is_favorite),
                        widget_count=count, created_at=d.created_at, updated_at=d.updated_at)


def _to_detail(d, widgets):
    return DashboardDetail(id=d.id, title=d.title, description=d.description, tags=d.tags or [],
                           refresh_interval=d.refresh_interval or 0, is_favorite=bool(d.is_favorite),
                           widgets=[WidgetOut.model_validate(w) for w in widgets],
                           created_at=d.created_at, updated_at=d.updated_at)