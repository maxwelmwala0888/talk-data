from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.sql import func
from app.db.base import Base


class DataRule(Base):
    __tablename__ = "data_rules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Scope: dataset_id NULL = global user rule; set = tied to a dataset
    dataset_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"), nullable=True, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # The actual boolean expression, evaluated with pandas df.eval
    # Example: "signup_date <= last_login_date"
    check = Column(Text, nullable=False)

    severity = Column(String(20), default="error")     # error | warning | info
    message = Column(Text, nullable=True)              # shown when violated
    suggested_fix = Column(String(50), nullable=True)  # drop | null | review

    # Metadata
    is_active = Column(Boolean, default=True)
    is_ai_generated = Column(Boolean, default=False)
    columns_used = Column(JSON, nullable=True, default=list)  # for safety: which columns this rule touches

    # Stats (updated on each validation)
    last_violation_count = Column(Integer, default=0)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())