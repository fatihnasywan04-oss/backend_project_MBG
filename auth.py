
import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash
from jwt.exceptions import InvalidTokenError

load_dotenv()

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

try:
    JWT_EXPIRE_MINUTES = int(
        os.getenv("JWT_EXPIRE_MINUTES", "60")
    )
except ValueError:
    raise RuntimeError(
        "JWT_EXPIRE_MINUTES harus berupa angka."
    )

if not JWT_SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY belum diatur di file .env"
    )

if len(JWT_SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY minimal 32 karakter."
    )

if JWT_EXPIRE_MINUTES <= 0:
    raise RuntimeError(
        "JWT_EXPIRE_MINUTES harus lebih besar dari 0."
    )

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password tidak boleh kosong.")

    return password_hash.hash(password)


def verify_password(
    password: str,
    hashed_password: str
) -> bool:
    if not password or not hashed_password:
        return False

    try:
        return password_hash.verify(
            password,
            hashed_password
        )
    except Exception:
        return False


def create_access_token(
    user_id: int,
    role: str
) -> str:
    if user_id <= 0:
        raise ValueError("User ID tidak valid.")

    if not role:
        raise ValueError("Role tidak boleh kosong.")

    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(
            minutes=JWT_EXPIRE_MINUTES
        ),
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    if not token:
        raise InvalidTokenError("Token kosong.")

    return jwt.decode(
        token,
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={
            "require": ["sub", "iat", "exp"],
        },
    )
