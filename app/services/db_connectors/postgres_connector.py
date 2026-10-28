import pandas as pd
from sqlalchemy import create_engine, inspect, text
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector


class PostgresConnector(BaseConnector):
    def __init__(self, host: str, port: int, database_name: str, username: str, password: str, **kwargs):
        super().__init__(**kwargs)
        self.url = f"postgresql://{username}:{password}@{host}:{port}/{database_name}"
        self.engine = create_engine(self.url, pool_pre_ping=True, connect_args={"connect_timeout": 10})

    def test_connection(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_schema(self) -> List[Dict[str, str]]:
        inspector = inspect(self.engine)
        schema_items = []
        for table_name in inspector.get_table_names():
            for column in inspector.get_columns(table_name):
                schema_items.append({
                    "table_name": table_name,
                    "column_name": column["name"],
                    "column_type": str(column["type"]),
                })
        return schema_items

    def execute_query(self, sql: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def get_dialect(self) -> str:
        return "postgres"