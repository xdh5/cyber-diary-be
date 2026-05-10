from fastapi import APIRouter

from app.api.v1.endpoints import auth, entries, food, upload

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(entries.router, prefix="/entries", tags=["entries"])
api_router.include_router(food.router, prefix="/food", tags=["food"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])