import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import engine

from routes.auth_routes import router as auth_router
from routes.event_routes import router as event_router
from routes.ticket_class_routes import router as ticket_class_router
from routes.order_routes import router as order_router
from routes.payment_routes import router as payment_router
from routes.admin_routes import router as admin_router


# =========================================================
# ENVIRONMENT CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="API Event Ticketers",
    description="Backend aplikasi pembelian tiket event",
    version="1.0.0",
)


# =========================================================
# CORS CONFIGURATION
# =========================================================

# Contoh .env:
# CORS_ORIGINS=http://localhost:3000,https://domain-frontend.com

cors_origins_env = os.getenv("CORS_ORIGINS", "*").strip()

if cors_origins_env == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [
        origin.strip().rstrip("/")
        for origin in cors_origins_env.split(",")
        if origin.strip()
    ]

    if not allowed_origins:
        raise RuntimeError(
            "CORS_ORIGINS tidak boleh kosong. "
            "Isi dengan '*' atau daftar URL frontend."
        )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# EVENT POSTER UPLOAD DIRECTORY
# =========================================================

UPLOAD_DIR = BASE_DIR / "uploads"
EVENT_UPLOAD_DIR = UPLOAD_DIR / "events"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EVENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/uploads",
    StaticFiles(directory=str(UPLOAD_DIR)),
    name="uploads",
)


# =========================================================
# HOME
# GET /
# =========================================================

@app.get("/")
def home():
    return {
        "message": "Backend Event Ticketers berhasil dijalankan",
        "status": "success",
    }


# =========================================================
# GENERAL HEALTH CHECK
# GET /health
# =========================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
    }


# =========================================================
# DATABASE HEALTH CHECK
# GET /health/database
# =========================================================

@app.get("/health/database")
def check_database():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "connected",
            "database": "PostgreSQL",
            "message": "Koneksi database berhasil",
        }

    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Koneksi database gagal",
        )


# =========================================================
# ROUTER REGISTRATION
# =========================================================

app.include_router(auth_router)
app.include_router(event_router)
app.include_router(ticket_class_router)
app.include_router(order_router)
app.include_router(payment_router)
app.include_router(admin_router)