from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.sql import func
from app.db.base import Base


class Widget(Base):
    """A single visual (chart / number / table) that belongs to a dashboard."""
    __tablename__ = "widgets"

    id = Column(Integer, primary_key=True, index=True)
    dashboard_id = Column(Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=True)             # original user question
    sql_query = Column(Text, nullable=True)          # generated SQL
    chart_config = Column(JSON, nullable=True)       # chart type, axes, colors, title
    widget_type = Column(String(50), default="chart")  # chart | number | table

    # Layout on the grid
    position_row = Column(Integer, default=0)
    position_col = Column(Integer, default=0)
    width = Column(Integer, default=6)    # grid units (out of 12)
    height = Column(Integer, default=4)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())