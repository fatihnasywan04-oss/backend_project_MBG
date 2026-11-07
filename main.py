
import asyncio
import logging
import os

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


# ============================================================
# ENVIRONMENT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Load .env sebelum mengimpor modul database.
load_dotenv(BASE_DIR / ".env")


# ============================================================
# APPLICATION IMPORTS
# ============================================================

from database import engine
from event_cleanup import cleanup_expired_events

from routes.auth_routes import router as auth_router
from routes.event_routes import router as event_router
from routes.ticket_class_routes import router as ticket_class_router
from routes.order_routes import router as order_router
from routes.payment_routes import router as payment_router
from routes.admin_routes import router as admin_router


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# CORS CONFIGURATION
# ============================================================

cors_origins_env = os.getenv(
    "CORS_ORIGINS",
    "*",
)

CORS_ORIGINS = [
    origin.strip()
    for origin in cors_origins_env.split(",")
    if origin.strip()
]

# Wildcard origin tidak digunakan bersama credentials.
ALLOW_CREDENTIALS = "*" not in CORS_ORIGINS


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Mengelola proses saat FastAPI dimulai dan dihentikan.

    Saat startup:
    - Menjalankan event cleanup otomatis.

    Saat shutdown:
    - Menghentikan event cleanup.
    - Menutup koneksi database.
    """

    cleanup_task = asyncio.create_task(
        cleanup_expired_events(),
        name="hikari-event-cleanup",
    )

    app.state.cleanup_task = cleanup_task

    logger.info(
        "Backend Hikari berhasil dijalankan."
    )

    logger.info(
        "Event cleanup otomatis berhasil dijalankan."
    )

    try:
        yield

    finally:
        logger.info(
            "Menghentikan proses cleanup Hikari."
        )

        cleanup_task.cancel()

        try:
            await cleanup_task

        except asyncio.CancelledError:
            logger.info(
                "Event cleanup otomatis berhasil dihentikan."
            )

        except Exception:
            logger.exception(
                "Terjadi kesalahan saat menghentikan event cleanup."
            )

        # Tutup koneksi database saat aplikasi berhenti.
        try:
            engine.dispose()

            logger.info(
                "Koneksi database berhasil ditutup."
            )

        except Exception:
            logger.exception(
                "Gagal menutup koneksi database."
            )

        logger.info(
            "Backend Hikari berhasil dihentikan."
        )


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Hikari API",
    description=(
        "Backend API untuk aplikasi event dan ticketing Hikari."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# STATIC FILES
# ============================================================

UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

app.mount(
    "/uploads",
    StaticFiles(
        directory=str(UPLOAD_DIR),
    ),
    name="uploads",
)


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def home():
    return {
        "message": "Backend API Hikari berjalan.",
        "status": "success",
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "application": "Hikari API",
    }


# ============================================================
# DATABASE HEALTH CHECK
# ============================================================

@app.get("/health/database")
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT 1")
            )

        return {
            "status": "healthy",
            "database": "connected",
        }

    except SQLAlchemyError:
        logger.exception(
            "Database health check gagal."
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database tidak dapat diakses.",
        )


# ============================================================
# CLEANUP HEALTH CHECK
# ============================================================

@app.get("/health/cleanup")
def cleanup_health_check():
    cleanup_task = getattr(
        app.state,
        "cleanup_task",
        None,
    )

    if cleanup_task is None:
        return {
            "status": "unavailable",
            "cleanup": "not_started",
        }

    if cleanup_task.done():
        return {
            "status": "unhealthy",
            "cleanup": "stopped",
        }

    return {
        "status": "healthy",
        "cleanup": "running",
    }


# ============================================================
# ROUTERS
# ============================================================

app.include_router(auth_router)
app.include_router(event_router)
app.include_router(ticket_class_router)
app.include_router(order_router)
app.include_router(payment_router)
app.include_router(admin_router)
