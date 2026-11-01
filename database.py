import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker


# =========================================================
# ENVIRONMENT CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL or not DATABASE_URL.strip():
    raise RuntimeError(
        "DATABASE_URL belum diatur. "
        "Periksa file .env atau Environment Variables hosting."
    )

DATABASE_URL = DATABASE_URL.strip()


# =========================================================
# VALIDASI DATABASE URL
# =========================================================

try:
    parsed_url = make_url(DATABASE_URL)

    allowed_drivers = (
        "postgres",
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    )

    if parsed_url.drivername not in allowed_drivers:
        raise ValueError(
            "DATABASE_URL harus menggunakan PostgreSQL."
        )

    if not parsed_url.host or not parsed_url.database:
        raise ValueError(
            "DATABASE_URL harus memiliki hostname dan nama database."
        )

    if parsed_url.drivername == "postgres":
        parsed_url = parsed_url.set(
            drivername="postgresql"
        )

except Exception as exc:
    raise RuntimeError(
        "Format DATABASE_URL tidak valid. "
        "Periksa kembali connection string PostgreSQL/Neon."
    ) from exc


DATABASE_URL = parsed_url.render_as_string(
    hide_password=False
)


# =========================================================
# DATABASE POOL CONFIGURATION
# =========================================================

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "300"))
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

if POOL_SIZE < 1:
    raise RuntimeError("DB_POOL_SIZE minimal 1.")

if MAX_OVERFLOW < 0:
    raise RuntimeError("DB_MAX_OVERFLOW tidak boleh negatif.")

if POOL_TIMEOUT < 1 or POOL_RECYCLE < 1 or CONNECT_TIMEOUT < 1:
    raise RuntimeError(
        "Konfigurasi timeout database harus lebih besar dari 0."
    )


# =========================================================
# DATABASE ENGINE
# =========================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=POOL_RECYCLE,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    connect_args={
        "connect_timeout": CONNECT_TIMEOUT,
    },
)


# =========================================================
# SESSION CONFIGURATION
# =========================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# =========================================================
# SQLALCHEMY BASE
# =========================================================

Base = declarative_base()


# =========================================================
# DATABASE DEPENDENCY
# =========================================================

def get_db():
    db = SessionLocal()

    try:
        yield db

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# =========================================================
# TEST DATABASE CONNECTION
# =========================================================

def test_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    except Exception:
        return False