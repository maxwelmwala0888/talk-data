from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import alerts, auth, cache, cleaner, connections, dashboards, datasets, dependencies, history, pipelines, profile, query, rules, semantic
from app.db.session import engine
from app.db.base import Base
import app.models  # noqa


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

Base.metadata.create_all(bind=engine)

# ---------- AUTO MIGRATION ----------
def _auto_migrate():
    from sqlalchemy import inspect as _inspect, text as _sql
    inspector = _inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        actual_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual_cols:
                try:
                    col_type = column.type.compile(dialect=engine.dialect)
                except Exception:
                    col_type = "TEXT"
                ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
                try:
                    with engine.connect() as conn:
                        conn.execute(_sql(ddl))
                        conn.commit()
                    print(f"[auto-migrate] + {table_name}.{column.name} ({col_type})")
                except Exception as e:
                    print(f"[auto-migrate] ! {table_name}.{column.name}: {e}")


try:
    _auto_migrate()
except Exception as e:
    print(f"[auto-migration] outer failure: {e}")


# ---------- ROUTERS ----------
app.include_router(auth.router, prefix="/api/v1")
app.include_router(connections.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")           # <-- critical
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(cleaner.router, prefix="/api/v1")
app.include_router(rules.router, prefix="/api/v1")
app.include_router(semantic.router, prefix="/api/v1")
app.include_router(dashboards.router, prefix="/api/v1")
app.include_router(history.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(pipelines.router, prefix="/api/v1")
app.include_router(cache.router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@app.get("/", tags=["Root"])
def root():
    return {"message": "Talk to Data API - see /docs"}


@app.get("/debug/routes", tags=["Debug"])
def list_routes():
    """List every registered route."""
    out = []
    for r in app.routes:
        methods = list(getattr(r, "methods", []))
        out.append({"path": r.path, "methods": methods})
    return out

# ---------- PRELOAD CSV DATASETS ----------
def _preload_csvs():
    """Warm the DuckDB cache for every existing CSV so first dashboard open is fast."""
    try:
        from app.db.session import SessionLocal
        from app.models.connection import Connection
        from app.services.db_connectors.duckdb_service import DuckDBService
        from app.services.connector_cache import connector_cache
        import pandas as pd
        import os as _os

        db = SessionLocal()
        try:
            conns = db.query(Connection).filter(Connection.db_type == "duckdb").all()
            if not conns:
                return
            print(f"[preload] warming {len(conns)} CSV dataset(s)")
            for c in conns:
                if not c.file_path or not _os.path.exists(c.file_path):
                    continue
                try:
                    mtime = int(_os.path.getmtime(c.file_path))
                except OSError:
                    continue
                base = _os.path.splitext(_os.path.basename(c.file_path))[0]
                safe = "".join(ch if ch.isalnum() else "_" for ch in base).strip("_").lower() or "data"
                duck_dir = _os.path.join(".", "data", "duckdb_cache")
                _os.makedirs(duck_dir, exist_ok=True)
                db_path = _os.path.join(duck_dir, f"{safe}_{mtime}.duckdb")

                if _os.path.exists(db_path):
                    try:
                        connector = DuckDBService(db_path=db_path)
                        tables = [t[0] for t in connector.conn.execute("SHOW TABLES").fetchall()]
                        if "data" in tables:
                            connector_cache.set(db_path, connector)
                            print(f"[preload] ✓ {base} (cached)")
                            continue
                    except Exception:
                        pass

                # Build it
                print(f"[preload] building {base}...")
                ext = c.file_path.split(".")[-1].lower()
                if ext == "csv":
                    df = pd.read_csv(c.file_path)
                elif ext in ("xls", "xlsx"):
                    df = pd.read_excel(c.file_path)
                elif ext == "json":
                    df = pd.read_json(c.file_path)
                elif ext == "parquet":
                    df = pd.read_parquet(c.file_path)
                else:
                    continue
                df.columns = [
                    "".join(ch if ch.isalnum() else "_" for ch in str(col)).strip("_").lower() or f"col_{i}"
                    for i, col in enumerate(df.columns)
                ]
                connector = DuckDBService(df_map={"data": df}, db_path=db_path)
                connector_cache.set(db_path, connector)
                print(f"[preload] ✓ {base} built")
        finally:
            db.close()
    except Exception as e:
        print(f"[preload] failed: {e}")


try:
    _preload_csvs()
except Exception as e:
    print(f"[preload] outer failure: {e}")


    

# ---------- PRELOAD CSV DATASETS ----------
def _preload_csvs():
    """Warm the DuckDB cache for every existing CSV so first dashboard open is fast."""
    try:
        from app.db.session import SessionLocal
        from app.models.connection import Connection
        from app.services.db_connectors.duckdb_service import DuckDBService
        from app.services.connector_cache import connector_cache
        import pandas as pd
        import os as _os

        db = SessionLocal()
        try:
            conns = db.query(Connection).filter(Connection.db_type == "duckdb").all()
            if not conns:
                return
            print(f"[preload] warming {len(conns)} CSV dataset(s)")
            for c in conns:
                if not c.file_path or not _os.path.exists(c.file_path):
                    continue
                try:
                    mtime = int(_os.path.getmtime(c.file_path))
                except OSError:
                    continue
                base = _os.path.splitext(_os.path.basename(c.file_path))[0]
                safe = "".join(ch if ch.isalnum() else "_" for ch in base).strip("_").lower() or "data"
                duck_dir = _os.path.join(".", "data", "duckdb_cache")
                _os.makedirs(duck_dir, exist_ok=True)
                db_path = _os.path.join(duck_dir, f"{safe}_{mtime}.duckdb")

                if _os.path.exists(db_path):
                    try:
                        connector = DuckDBService(db_path=db_path)
                        tables = [t[0] for t in connector.conn.execute("SHOW TABLES").fetchall()]
                        if "data" in tables:
                            connector_cache.set(db_path, connector)
                            print(f"[preload] OK {base} (cached)")
                            continue
                    except Exception:
                        pass

                print(f"[preload] building {base}...")
                ext = c.file_path.split(".")[-1].lower()
                if ext == "csv":
                    df = pd.read_csv(c.file_path)
                elif ext in ("xls", "xlsx"):
                    df = pd.read_excel(c.file_path)
                elif ext == "json":
                    df = pd.read_json(c.file_path)
                elif ext == "parquet":
                    df = pd.read_parquet(c.file_path)
                else:
                    continue
                df.columns = [
                    "".join(ch if ch.isalnum() else "_" for ch in str(col)).strip("_").lower() or f"col_{i}"
                    for i, col in enumerate(df.columns)
                ]
                connector = DuckDBService(df_map={"data": df}, db_path=db_path)
                connector_cache.set(db_path, connector)
                print(f"[preload] OK {base} built")
        finally:
            db.close()
    except Exception as e:
        print(f"[preload] failed: {e}")


try:
    _preload_csvs()
except Exception as e:
    print(f"[preload] outer failure: {e}")