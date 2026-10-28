from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from app.db.session import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.alert import Alert

router = APIRouter(prefix="/alerts", tags=["Alerts"])


class AlertCreate(BaseModel):
    title: str
    message: Optional[str] = None
    severity: str = "info"


class AlertOut(BaseModel):
    id: int
    title: str
    message: Optional[str]
    severity: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Alert).filter(Alert.user_id == user.id).order_by(Alert.created_at.desc()).all()


@router.post("", response_model=AlertOut)
def create_alert(payload: AlertCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = Alert(user_id=user.id, title=payload.title, message=payload.message, severity=payload.severity)
    db.add(a); db.commit(); db.refresh(a)
    return a


@router.post("/{alert_id}/read")
def mark_read(alert_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(Alert).filter(Alert.id == alert_id, Alert.user_id == user.id).first()
    if not a: raise HTTPException(status_code=404, detail="Alert not found")
    a.is_read = True
    db.commit()
    return {"ok": True}


@router.delete("/{alert_id}")
def delete_alert(alert_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    a = db.query(Alert).filter(Alert.id == alert_id, Alert.user_id == user.id).first()
    if not a: raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(a); db.commit()
    return {"ok": True}

# ---------- UNREAD / BULK ----------

@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Return the number of unread alerts for the badge."""
    n = db.query(Alert).filter(
        Alert.user_id == user.id,
        Alert.is_read == False,  # noqa: E712
    ).count()
    return {"count": n}


@router.post("/mark-all-read")
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Mark every unread alert as read."""
    updated = db.query(Alert).filter(
        Alert.user_id == user.id,
        Alert.is_read == False,  # noqa: E712
    ).update({Alert.is_read: True})
    db.commit()
    return {"ok": True, "updated": updated}
