"""
FastAPI application factory with lifespan management.
"""
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.api.routes import router
from app.services.scheduler import start_scheduler, stop_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler:
    - On startup: create DB tables (dev convenience) and start the scheduler.
    - On shutdown: stop the scheduler and dispose of the DB engine.
    """
    # ── Startup ────────────────────────────────────
    logger.info("Starting Scraper Tracker API...")

    # Create tables if they don't exist (use Alembic migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")

    # Start the background price-checking scheduler
    start_scheduler()

    yield  # Application runs here

    # ── Shutdown ───────────────────────────────────
    stop_scheduler()
    await engine.dispose()
    logger.info("Scraper Tracker API shut down")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Scraper Tracker API",
        description="Monitor product prices on Tiki.vn and Shopee.vn with automatic Telegram alerts.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the React frontend (adjust origins in production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routes
    app.include_router(router)

    # ── Global exception handler — trả full traceback trong response ──
    @app.middleware("http")
    async def catch_exceptions_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error(
                "Unhandled exception on %s %s:\n%s",
                request.method, request.url.path, tb
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": str(exc),
                    "type": type(exc).__name__,
                    "path": str(request.url),
                    "traceback": tb,
                },
            )

    # Health check
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    return app
