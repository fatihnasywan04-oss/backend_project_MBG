
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

# Lokasi folder backend
BASE_DIR = Path(__file__).resolve().parent

# Membaca .env untuk pengembangan lokal.
# Di hosting, DATABASE_URL sebaiknya diatur melalui Environment Variables.
load_dotenv(BASE_DIR / ".env")

# Mengambil URL database
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL or not DATABASE_URL.strip():
    raise RuntimeError(
        "DATABASE_URL belum diatur. "
        "Periksa Environment Variables di dashboard hosting."
    )

DATABASE_URL = DATABASE_URL.strip()

# Memvalidasi URL tanpa menampilkan kredensialnya
try:
    parsed_url = make_url(DATABASE_URL)

    if parsed_url.drivername not in (
        "postgresql",
        "postgres",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    ):
        raise ValueError("DATABASE_URL harus menggunakan PostgreSQL.")

    if not parsed_url.host or not parsed_url.database:
        raise ValueError(
            "DATABASE_URL harus memiliki hostname dan nama database."
        )

except Exception as exc:
    raise RuntimeError(
        "Format DATABASE_URL tidak valid. "
        "Salin ulang connection string dari Neon Console."
    ) from exc

# Normalisasi nama driver PostgreSQL
if parsed_url.drivername == "postgres":
    parsed_url = parsed_url.set(drivername="postgresql")

DATABASE_URL = parsed_url.render_as_string(hide_password=False)

# Membuat engine database
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
)

# Membuat session database
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base untuk model SQLAlchemy
Base = declarative_base()


# Dependency untuk endpoint FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Fungsi untuk menguji koneksi database
def test_connection():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
