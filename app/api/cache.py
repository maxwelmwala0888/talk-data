from fastapi import APIRouter, Depends
from app.api.dependencies import get_current_user
from app.models.user import User
from app.services.cache_service import query_cache


router = APIRouter(prefix="/cache", tags=["Cache"])


@router.post("/clear")
def clear_cache(user: User = Depends(get_current_user)):
    """Clear the in-memory query cache."""
    size_before = query_cache.size()
    query_cache.clear()
    return {"ok": True, "cleared": size_before}


@router.get("/size")
def cache_size(user: User = Depends(get_current_user)):
    return {"size": query_cache.size()}