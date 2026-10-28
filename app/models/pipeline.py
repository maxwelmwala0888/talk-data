from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.sql import func
from app.db.base import Base


class Pipeline(Base):
    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # The connection this pipeline reads from (required)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                           nullable=True, index=True)

    name = Column(String(255), nullable=False)

    # Legacy label fields (kept for backward compat — no longer required)
    source = Column(String(100), nullable=True)
    target = Column(String(100), nullable=True)

    # Pipeline definition
    description = Column(Text, nullable=True)
    steps = Column(Text, nullable=True)      # JSON string of cleaning/transform steps

    # Optional: dashboard to refresh after run
    dashboard_id = Column(Integer, ForeignKey("dashboards.id", ondelete="SET NULL"),
                          nullable=True, index=True)

    # Optional: SQL to compute the metric (default: SELECT COUNT(*) FROM data)
    metric_query = Column(Text, nullable=True)

    # Metric tracking
    last_metric_value = Column(Integer, nullable=True)  # stored as int for safety
    last_metric_at = Column(DateTime(timezone=True), nullable=True)
    schedule = Column(String(100), nullable=True)  # "manual" | "hourly" | "daily" | "weekly"
    alert_threshold_pct = Column(Integer, default=10)  # alert if metric changes > N%

    is_active = Column(Boolean, default=True)
    last_run = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String(20), nullable=True)  # "success" | "failed" | "running"
    last_error = Column(Text, nullable=True)
    run_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())