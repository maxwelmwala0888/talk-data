from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, Any, Dict
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.query_history import QueryHistory

router = APIRouter(prefix="/history", tags=["History"])

class HistoryOut(BaseModel):
    id: int
    connection_id: Optional[int]
    prompt: str
    sql: Optional[str]
    row_count: int
    payload: Optional[Dict[str, Any]] = None
    created_at: datetime
    class Config:
        from_attributes = True

@router.get("", response_model=list[HistoryOut])
def list_history(limit: int = 20, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    return db.query(QueryHistory).filter(QueryHistory.user_id == user.id)\
             .order_by(QueryHistory.created_at.desc()).limit(limit).all()

@router.delete("/{history_id}")
def delete_history(history_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    h = db.query(QueryHistory).filter(QueryHistory.id == history_id,
                                      QueryHistory.user_id == user.id).first()
    if not h: raise HTTPException(status_code=404, detail="Not found")
    db.delete(h); db.commit()
    return {"ok": True}

@router.delete("")
def clear_history(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(QueryHistory).filter(QueryHistory.user_id == user.id).delete()
    db.commit()
    return {"ok": True}