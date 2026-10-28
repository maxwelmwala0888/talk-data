import os
import re
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.connection import Connection
from app.models.query_history import QueryHistory
from app.schemas.query import QueryRequest, QueryResponse, ChartConfig
from app.core.security import decrypt_credential
from app.services.db_connectors.postgres_connector import PostgresConnector
from app.services.db_connectors.mysql_connector import MySQLConnector
from app.services.db_connectors.sqlserver_connector import SQLServerConnector
from app.services.db_connectors.snowflake_connector import SnowflakeConnector
from app.services.db_connectors.bigquery_connector import BigQueryConnector
from app.services.db_connectors.databricks_connector import DatabricksConnector
from app.services.db_connectors.access_connector import AccessConnector
from app.services.db_connectors.duckdb_service import DuckDBService
from app.services.chains.text_to_sql_chain import generate_sql, fix_sql, is_safe_sql
from app.services.chains.insight_chain import generate_chart_and_insights, detect_chart_hint
from app.services.chains.natural_answer import classify_response_mode, generate_natural_answer
from app.services.chains.embed_chain import retrieve_relevant_schema
from app.services.cache_service import query_cache

router = APIRouter(prefix="/query", tags=["Query"])
RAG_MIN_COLUMNS = 30


def _read_dataframe(file_path):
    ext = file_path.split(".")[-1].lower()
    if ext == "csv": df = pd.read_csv(file_path)
    elif ext in ("xls", "xlsx"): df = pd.read_excel(file_path)
    elif ext == "json": df = pd.read_json(file_path)
    elif ext == "parquet": df = pd.read_parquet(file_path)
    else: raise HTTPException(400, f"Unsupported file: {ext}")
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


def _load_duckdb_connector(conn):
    if not conn.file_path or not os.path.exists(conn.file_path):
        raise HTTPException(404, "Dataset file not found")
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


def _get_connector(conn):
    if conn.db_type == "postgres":
        return PostgresConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                 username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "mysql":
        return MySQLConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                              username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "sqlserver":
        return SQLServerConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                  username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "snowflake":
        return SnowflakeConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                  username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "bigquery":
        return BigQueryConnector(database_name=conn.database_name,
                                 password=decrypt_credential(conn.encrypted_password) if conn.encrypted_password else None)
    if conn.db_type == "databricks":
        return DatabricksConnector(host=conn.host, port=conn.port, database_name=conn.database_name,
                                   username=conn.username, password=decrypt_credential(conn.encrypted_password))
    if conn.db_type == "duckdb": return _load_duckdb_connector(conn)
    if conn.db_type == "access": return AccessConnector(file_path=conn.file_path)
    raise HTTPException(400, f"Unsupported db_type: {conn.db_type}")


def _python_ranking_fallback(conn, prompt, current_df):
    if current_df is not None and len(current_df) > 1:
        return None
    if conn.db_type not in ("duckdb", "access"):
        return None
    q_lower = prompt.lower()
    wants_ranking = any(kw in q_lower for kw in [
        "top", "rank", "by ", "per ", "group", "compare", "highest", "lowest", "most", "least"
    ])
    if not wants_ranking:
        return None
    print(f"[fallback-ranking] retrying in pandas")
    try:
        # Use a real connector for non-CSV sources (Access, etc.)
        if conn.db_type == "access":
            from app.services.db_connectors.access_connector import AccessConnector
            acc = AccessConnector(file_path=conn.file_path)
            # AccessConnector wraps everything in DuckDB; run a SELECT * to get the data
            schema = acc.get_schema()
            if not schema:
                return None
            table_name = schema[0]["table_name"]
            raw_df = acc.execute_query(f'SELECT * FROM "{table_name}"')
        elif conn.db_type == "duckdb":
            raw_df = _read_dataframe(conn.file_path)
        else:
            return None
    except Exception as e:
        print(f"[fallback-ranking] cannot read file: {e}")
        return None
    numeric_cols = raw_df.select_dtypes(include="number").columns.tolist()
    cat_cols = [c for c in raw_df.columns if c not in numeric_cols]
    target_cat = None
    for c in cat_cols:
        if c.lower() in q_lower or c.lower().replace("_", " ") in q_lower:
            target_cat = c
            break
    if target_cat is None and cat_cols:
        target_cat = cat_cols[0]
    m = re.search(r"top\s+(\d+)", q_lower)
    n = int(m.group(1)) if m else 10
    agg = "count"
    if any(k in q_lower for k in ["sum", "total", "revenue", "sales", "amount"]): agg = "sum"
    elif any(k in q_lower for k in ["avg", "average", "mean"]): agg = "mean"
    target_val = numeric_cols[0] if numeric_cols else None
    for col in numeric_cols:
        if col.lower() in q_lower or col.lower().replace("_", " ") in q_lower:
            target_val = col
            break
    if not target_cat: return None
    if agg == "count":
        grouped = raw_df.groupby(target_cat, as_index=False).size().rename(columns={"size": "count"})
    elif target_val:
        grouped = raw_df.groupby(target_cat, as_index=False)[target_val].agg(agg)
        grouped = grouped.rename(columns={target_val: f"{agg}_{target_val}"})
    else:
        return None
    val_col = grouped.columns[1]
    grouped = grouped.sort_values(val_col, ascending=False).head(n).reset_index(drop=True)
    print(f"[fallback-ranking] pandas produced {len(grouped)} rows")
    return grouped


@router.post("", response_model=QueryResponse)
def run_query(payload: QueryRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.connection_id: raise HTTPException(400, "connection_id is required")
    conn = db.query(Connection).filter(Connection.id == payload.connection_id, Connection.user_id == user.id).first()
    if not conn: raise HTTPException(404, "Connection not found")

    chart_hint = detect_chart_hint(payload.prompt) or ""
    print(f"[chart hint] {chart_hint or '(none)'}")

    cache_key = (payload.prompt.strip().lower(), conn.id, conn.db_type, payload.include_insights, chart_hint)
    if payload.use_cache:
        cached = query_cache.get(*cache_key)
        if cached:
            print(f"[cache] HIT: {payload.prompt[:60]}")
            return QueryResponse(**cached, cached=True)
        print(f"[cache] MISS: {payload.prompt[:60]}")

    connector = _get_connector(conn)
    full_schema = connector.get_schema()
    if not full_schema: raise HTTPException(400, "No schema found")

    dialect = connector.get_dialect()
    schema_for_llm = full_schema
    if len(full_schema) >= RAG_MIN_COLUMNS:
        try:
            retrieved = retrieve_relevant_schema(db, conn.id, payload.prompt)
            if retrieved: schema_for_llm = retrieved
        except Exception: pass

    try:
        sql = generate_sql(payload.prompt, schema_for_llm, dialect=dialect, chart_hint=chart_hint)
        print(f"[sql] {sql[:250]}")
    except Exception as e:
        raise HTTPException(429, f"AI error: {str(e)[:200]}")

    if not is_safe_sql(sql):
        raise HTTPException(400, f"SQL not read-only: {sql[:200]}")

    df = None
    err = None
    try: df = connector.execute_query(sql)
    except Exception as e: err = str(e)

    if df is None and err:
        try:
            sql2 = fix_sql(payload.prompt, sql, err, schema_for_llm, dialect=dialect, chart_hint=chart_hint)
            if is_safe_sql(sql2):
                try: df = connector.execute_query(sql2); sql = sql2; err = None
                except Exception as e2: err = str(e2)
        except Exception: pass

    if df is None or len(df) <= 1:
        fb = _python_ranking_fallback(conn, payload.prompt, df)
        if fb is not None and len(fb) > 1:
            df = fb
            sql = f"-- Python fallback: {sql}"
            err = None

    if df is None:
        raise HTTPException(400, f"Query failed.\nSQL:\n{sql}\n\nError: {err}")

    columns = df.columns.tolist()
    safe_rows = _clean_rows(df)

    # ---- Decide: natural language or chart? ----
    response_mode = classify_response_mode(payload.prompt, columns, safe_rows)
    natural_answer = None

    # Always generate a friendly summary unless the user explicitly asked for a chart
    chart_keywords = (
        "chart", "plot", "graph", "trend", "visuali", "bar ", "pie ",
        "line ", "scatter", "histogram", "donut", "heatmap", "radar",
        "funnel", "treemap",
    )
    wants_chart = any(kw in (payload.prompt or "").lower() for kw in chart_keywords)

    if not wants_chart and safe_rows:
        try:
            natural_answer = generate_natural_answer(payload.prompt, columns, safe_rows)
            print(f"[query] summary -> '{natural_answer[:80]}'")
        except Exception as e:
            print(f"[query] natural answer failed: {e}")
            natural_answer = None

    try:
        combined = generate_chart_and_insights(payload.prompt, columns, safe_rows, include_insights=payload.include_insights)
    except Exception:
        combined = {"chart_type": "table", "x_axis": None, "y_axis": None, "color_scheme": "vibrant",
                    "title": None, "x_label": None, "y_label": None, "insights": []}

    if chart_hint: combined["chart_type"] = chart_hint

    chart_config = ChartConfig(
        chart_type=combined.get("chart_type", "table"),
        x_axis=combined.get("x_axis"), y_axis=combined.get("y_axis"),
        color_scheme=combined.get("color_scheme", "vibrant"),
        title=combined.get("title"), x_label=combined.get("x_label"), y_label=combined.get("y_label"))
    insights = combined.get("insights", []) if payload.include_insights else []

    payload_out = {"sql": sql, "columns": columns, "rows": safe_rows, "row_count": len(df),
                   "chart_config": chart_config.model_dump(), "insights": insights,
                   "response_mode": response_mode,
                   "natural_answer": natural_answer,
                   "auto_dashboard_id": None, "auto_widget_id": None}

    try:
        hist = QueryHistory(user_id=user.id, connection_id=conn.id, prompt=payload.prompt,
                            sql=sql, row_count=len(df), payload=payload_out)
        db.add(hist); db.commit()
    except Exception:
        db.rollback()

    # NOTE: No auto-attach. Widgets are only added when user clicks Save.

    if payload.use_cache and len(safe_rows) <= 500:
        query_cache.set(payload_out, *cache_key)

    return QueryResponse(**payload_out, cached=False)