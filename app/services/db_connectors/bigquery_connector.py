import pandas as pd
from typing import List, Dict
from app.services.db_connectors.base_connector import BaseConnector


class BigQueryConnector(BaseConnector):
    """
    Requires a GCP service-account JSON file.
    Pass its path as the `password` field (or via `credentials_json` kwarg).
    """
    def __init__(self, database_name: str, password: str = None, credentials_json: str = None, **kwargs):
        super().__init__(**kwargs)
        from google.cloud import bigquery
        from google.oauth2 import service_account

        creds_path = credentials_json or password
        if creds_path and creds_path.strip().endswith(".json"):
            creds = service_account.Credentials.from_service_account_file(creds_path)
            self.client = bigquery.Client(project=database_name, credentials=creds)
        else:
            self.client = bigquery.Client(project=database_name)
        self.project = database_name

    def test_connection(self) -> bool:
        try:
            list(self.client.list_datasets(max_results=1))
            return True
        except Exception: return False

    def get_schema(self) -> List[Dict[str, str]]:
        items = []
        for dataset in self.client.list_datasets():
            for table in self.client.list_tables(dataset.dataset_id):
                full_table = self.client.get_table(f"{dataset.dataset_id}.{table.table_id}")
                for field in full_table.schema:
                    items.append({
                        "table_name": f"{dataset.dataset_id}.{table.table_id}",
                        "column_name": field.name,
                        "column_type": field.field_type,
                    })
        return items

    def execute_query(self, sql: str) -> pd.DataFrame:
        return self.client.query(sql).to_dataframe()

    def get_dialect(self) -> str: return "bigquery"