from fastapi import APIRouter
router = APIRouter(prefix="/api/v1/trust", tags=["trust-safety"])
@router.get("/health")
async def health():
    return {"status": "ok"}
