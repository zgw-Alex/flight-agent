"""FastAPI application entrypoint for the backend."""

from fastapi import FastAPI

from flight_agent.api.health import router as health_router


def create_app() -> FastAPI:
    """Create the backend ASGI application and wire outer transport routes."""
    app = FastAPI(title="Flight Agent Backend")
    app.include_router(health_router)

    return app


app = create_app()
