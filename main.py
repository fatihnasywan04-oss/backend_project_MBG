import asyncio
import logging
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

# Load .env sebelum mengimpor modul yang menggunakan database.
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(
        cleanup_expired_events()
    )

    logger.info(
        "Event cleanup otomatis berhasil dijalankan."
    )

    try:
        yield

    finally:
        cleanup_task.cancel()

        try:
            await cleanup_task

        except asyncio.CancelledError:
            logger.info(
                "Event cleanup otomatis dihentikan."
            )


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Hikari API",
    description="Backend API untuk aplikasi event dan ticketing Hikari.",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    StaticFiles(directory=str(UPLOAD_DIR)),
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
    }


@app.get("/health/database")
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

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
# ROUTERS
# ============================================================

app.include_router(auth_router)
app.include_router(event_router)
app.include_router(ticket_class_router)
app.include_router(order_router)
app.include_router(payment_router)
app.include_router(admin_router)