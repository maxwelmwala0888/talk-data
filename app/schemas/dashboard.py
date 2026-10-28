from pydantic import BaseModel
from typing import Optional, Any, Dict, List
from datetime import datetime
from app.schemas.widget import WidgetOut


class DashboardCreate(BaseModel):
    title: str
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    refresh_interval: int = 0


class DashboardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    refresh_interval: Optional[int] = None
    is_favorite: Optional[bool] = None


class DashboardOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    refresh_interval: int = 0
    is_favorite: bool = False
    widget_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DashboardDetail(BaseModel):
    """A dashboard with all its widgets loaded."""
    id: int
    title: str
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    refresh_interval: int = 0
    is_favorite: bool = False
    widgets: List[WidgetOut] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True