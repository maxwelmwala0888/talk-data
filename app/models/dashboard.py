from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.sql import func
from app.db.base import Base


class Dashboard(Base):
    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Legacy fields kept for backward compat
    prompt = Column(Text, nullable=True)
    sql_query = Column(Text, nullable=True)
    chart_config = Column(JSON, nullable=True)
    connection_id = Column(Integer, ForeignKey("connections.id"), nullable=True)

    # Power BI-style fields
    layout = Column(JSON, nullable=True)
    refresh_interval = Column(Integer, default=0)
    is_favorite = Column(Boolean, default=False)
    tags = Column(JSON, nullable=True)

    # NEW: If this dashboard was auto-created for a dataset, this points to that connection
    auto_for_connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                                    nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())