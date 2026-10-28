from abc import ABC, abstractmethod
from typing import List, Dict
import pandas as pd


class BaseConnector(ABC):
    def __init__(self, **kwargs):
        self.config = kwargs

    @abstractmethod
    def test_connection(self) -> bool: pass

    @abstractmethod
    def get_schema(self) -> List[Dict[str, str]]: pass

    @abstractmethod
    def execute_query(self, sql: str) -> pd.DataFrame: pass

    def get_dialect(self) -> str:
        return "postgres"