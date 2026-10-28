from pydantic import BaseModel
from typing import Optional, Any, List, Dict


class QueryRequest(BaseModel):
    prompt: str
    connection_id: Optional[int] = None
    use_cache: bool = True
    include_insights: bool = True


class ChartConfig(BaseModel):
    chart_type: str
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    series: Optional[List[str]] = None
    color_scheme: Optional[str] = "vibrant"
    title: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None


class QueryResponse(BaseModel):
    response_mode: str = "complex"   # "simple" | "complex"
    natural_answer: str | None = None  # filled when response_mode == "simple"
    sql: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    chart_config: Optional[ChartConfig] = None
    insights: Optional[List[str]] = None
    cached: bool = False
    auto_dashboard_id: Optional[int] = None
    auto_widget_id: Optional[int] = None