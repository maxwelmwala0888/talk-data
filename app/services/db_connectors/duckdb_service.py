import duckdb, pandas as pd, os
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector


class DuckDBService(BaseConnector):
    """Thread-safe DuckDB connector. Each instance owns its own connection."""

    def __init__(self, df_map: Dict[str, pd.DataFrame] = None,
                 db_path: str = None, **kwargs):
        super().__init__(**kwargs)
        self._df_refs = {}
        self.db_path = db_path

        if df_map and db_path:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            # If the file already exists, just open it read-only
            if os.path.exists(db_path):
                try:
                    self.conn = duckdb.connect(database=db_path, read_only=True)
                    return
                except Exception:
                    pass
            # Build the file
            write_conn = duckdb.connect(database=db_path)
            for tn, df in df_map.items():
                self._materialize(write_conn, tn, df)
            write_conn.close()
            self.conn = duckdb.connect(database=db_path, read_only=True)

        elif db_path and os.path.exists(db_path):
            self.conn = duckdb.connect(database=db_path, read_only=True)

        elif df_map:
            self.conn = duckdb.connect(database=":memory:")
            for tn, df in df_map.items():
                self._materialize(self.conn, tn, df)

        else:
            self.conn = duckdb.connect(database=":memory:")

    def _materialize(self, conn, table_name, df):
        self._df_refs[table_name] = df
        tmp = f"__tmp_{table_name}_{os.urandom(4).hex()}"
        conn.register(tmp, df)
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        except Exception:
            pass
        conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM "{tmp}"')
        try:
            conn.unregister(tmp)
        except Exception:
            pass

    def test_connection(self) -> bool:
        try:
            self.conn.execute("SELECT 1").fetchall()
            return True
        except Exception:
            return False

    def get_schema(self) -> List[Dict[str, str]]:
        items = []
        for (tn,) in self.conn.execute("SHOW TABLES").fetchall():
            for col in self.conn.execute(f'DESCRIBE "{tn}"').fetchall():
                items.append({"table_name": tn, "column_name": col[0], "column_type": col[1]})
        return items

    def execute_query(self, sql: str) -> pd.DataFrame:
        return self.conn.execute(sql).fetchdf()

    def get_dialect(self) -> str:
        return "duckdb"