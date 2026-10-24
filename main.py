
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import engine

from routes.auth_routes import router as auth_router
from routes.event_routes import router as event_router
from routes.order_routes import router as order_router
from routes.payment_routes import router as payment_router


app = FastAPI(
    title="API Aplikasi Tiket",
    description="Backend aplikasi pembelian tiket event",
    version="1.0.0",
)


# Konfigurasi CORS untuk Flutter Web.
# Untuk produksi, sesuaikan allow_origins dengan domain frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "message": "Backend Aplikasi Tiket berhasil dijalankan",
        "status": "success",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


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
            status_code=503,
            detail="Koneksi database gagal",
        )


# Mendaftarkan setiap router satu kali.
app.include_router(auth_router)
app.include_router(event_router)
app.include_router(order_router)
app.include_router(payment_router)
