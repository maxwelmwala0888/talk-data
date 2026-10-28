import pandas as pd
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector


class DatabricksConnector(BaseConnector):
    def __init__(self, host: str, port: int = 443, database_name: str = "main",
                 username: str = "token", password: str = "", http_path: str = None,
                 schema: str = "default", **kwargs):
        super().__init__(**kwargs)
        self.host = (host or "").replace("https://", "").replace("http://", "").strip("/")
        self.port = port or 443
        self.catalog = database_name or "main"
        self.schema = schema or "default"
        self.token = password
        if http_path:
            self.http_path = http_path
        elif username and username.startswith("/sql"):
            self.http_path = username
        else:
            self.http_path = "/sql/1.0/warehouses/your-warehouse-id"
        self._last_error = None

    def _connect(self):
        from databricks import sql
        return sql.connect(
            server_hostname=self.host,
            http_path=self.http_path,
            access_token=self.token,
            _socket_timeout=15,
        )

    def test_connection(self) -> bool:
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            conn.close()
            self._last_error = None
            return True
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            print(f"[databricks] connection failed: {self._last_error}")
            return False

    def get_schema(self) -> List[Dict[str, str]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SHOW TABLES IN {self.catalog}.{self.schema}")
            tables = [row[1] for row in cur.fetchall()]
            items = []
            for t in tables[:50]:
                try:
                    cur.execute(f"DESCRIBE {self.catalog}.{self.schema}.{t}")
                    for row in cur.fetchall():
                        col_name = row[0]
                        col_type = row[1] if len(row) > 1 else ""
                        if col_name and not str(col_name).startswith("#"):
                            items.append({
                                "table_name": t,
                                "column_name": col_name,
                                "column_type": col_type,
                            })
                except Exception:
                    continue
            cur.close()
            return items
        finally:
            conn.close()

    def execute_query(self, sql: str) -> pd.DataFrame:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows, columns=cols)
        finally:
            conn.close()

    def get_dialect(self) -> str:
        return "databricks"


def build_connector_debug(db_type: str, config: dict):
    """Return (connector, error_string_or_None). Used by /connections/test for better errors."""
    if db_type != "databricks":
        return None, None
    try:
        return DatabricksConnector(**config), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"