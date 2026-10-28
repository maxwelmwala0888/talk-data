from pydantic import BaseModel
from typing import Optional, Any, Dict
from datetime import datetime


class WidgetCreate(BaseModel):
    title: str
    prompt: Optional[str] = None
    sql_query: Optional[str] = None
    chart_config: Optional[Dict[str, Any]] = None
    connection_id: Optional[int] = None
    widget_type: str = "chart"
    position_row: int = 0
    position_col: int = 0
    width: int = 6
    height: int = 4


class WidgetUpdate(BaseModel):
    title: Optional[str] = None
    chart_config: Optional[Dict[str, Any]] = None
    position_row: Optional[int] = None
    position_col: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class WidgetOut(BaseModel):
    id: int
    dashboard_id: int
    connection_id: Optional[int]
    title: str
    prompt: Optional[str]
    sql_query: Optional[str]
    chart_config: Optional[Dict[str, Any]]
    widget_type: str
    position_row: int
    position_col: int
    width: int
    height: int
    created_at: datetime

    class Config:
        from_attributes = True