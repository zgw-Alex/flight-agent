"""FastAPI application entrypoint for the backend."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create the minimal backend ASGI application."""
    app = FastAPI(title="Flight Agent Backend")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
