"""Health-check transport route."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Return a stable local health payload without touching core business logic."""
    return {"status": "ok"}
