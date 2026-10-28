from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.sql import func
from app.db.base import Base

class QueryHistory(Base):
    __tablename__ = "query_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True)
    prompt = Column(Text, nullable=False)
    sql = Column(Text, nullable=True)
    row_count = Column(Integer, default=0)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())