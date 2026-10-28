import pandas as pd
import snowflake.connector
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector


class SnowflakeConnector(BaseConnector):
    def __init__(self, host: str, port: int, database_name: str, username: str, password: str, **kwargs):
        super().__init__(**kwargs)
        self.account = host
        self.warehouse = kwargs.get("warehouse", "COMPUTE_WH")
        self.schema = kwargs.get("schema", "PUBLIC")
        self.database_name = database_name
        self.username = username
        self.password = password
        self.port = port or 443

    def _connect(self):
        return snowflake.connector.connect(
            account=self.account, user=self.username, password=self.password,
            database=self.database_name, warehouse=self.warehouse, schema=self.schema,
            port=self.port, login_timeout=10,
        )

    def test_connection(self) -> bool:
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close(); conn.close()
            return True
        except Exception: return False

    def get_schema(self) -> List[Dict[str, str]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{self.schema}'
                ORDER BY TABLE_NAME, ORDINAL_POSITION
            """)
            items = [{"table_name": r[0], "column_name": r[1], "column_type": r[2]} for r in cur.fetchall()]
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

    def get_dialect(self) -> str: return "snowflake"