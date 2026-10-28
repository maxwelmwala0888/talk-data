import os
import subprocess
import pandas as pd
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector
from app.services.db_connectors.duckdb_service import DuckDBService


class AccessConnector(BaseConnector):
    def __init__(self, file_path: str, **kwargs):
        super().__init__(**kwargs)
        self.file_path = file_path
        self.duck = None
        self._load()

    def _load(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Access file not found: {self.file_path}")
        dfs = self._load_via_pyodbc() if os.name == "nt" else self._load_via_mdbtools()
        self.duck = DuckDBService(df_map=dfs)

    def _load_via_pyodbc(self) -> Dict[str, pd.DataFrame]:
        import pyodbc
        conn_str = r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};" + rf"DBQ={os.path.abspath(self.file_path)};"
        conn = pyodbc.connect(conn_str, read_only=True)
        cursor = conn.cursor()
        tables = [r.table_name for r in cursor.tables(tableType="TABLE").fetchall()]
        dfs = {t: pd.read_sql(f"SELECT * FROM [{t}]", conn) for t in tables}
        conn.close()
        return dfs

    def _load_via_mdbtools(self) -> Dict[str, pd.DataFrame]:
        from io import StringIO
        try:
            result = subprocess.run(["mdb-tables", "-1", self.file_path],
                                    capture_output=True, text=True, check=True)
        except FileNotFoundError:
            raise RuntimeError("mdbtools not installed. On Ubuntu: sudo apt install mdbtools")
        tables = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
        dfs = {}
        for t in tables:
            csv = subprocess.run(["mdb-export", self.file_path, t],
                                 capture_output=True, text=True, check=True).stdout
            dfs[t] = pd.read_csv(StringIO(csv))
        return dfs

    def test_connection(self) -> bool:
        return self.duck is not None

    def get_schema(self) -> List[Dict[str, str]]:
        return self.duck.get_schema()

    def execute_query(self, sql: str) -> pd.DataFrame:
        return self.duck.execute_query(sql)

    def get_dialect(self) -> str:
        return "duckdb"