import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Order, Payment, User
from routes.auth_routes import get_current_user
from order_stock import restore_order_stock


router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)

logger = logging.getLogger(__name__)

# Harus sama dengan routes/order_routes.py.
EXPIRATION_SECONDS = 60


# ============================================================
# SCHEMAS
# ============================================================

class PaymentCreate(BaseModel):
    order_id: int = Field(gt=0)

    payment_method: Literal[
        "transfer_bank",
        "e_wallet",
        "qris",
    ]

    payment_provider: str = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("payment_method", mode="before")
    @classmethod
    def normalize_payment_method(cls, value):
        if value is None:
            return value

        normalized = str(value).strip().lower()

        aliases = {
            "bank_transfer": "transfer_bank",
            "transfer": "transfer_bank",
            "ewallet": "e_wallet",
            "e-wallet": "e_wallet",
        }

        return aliases.get(normalized, normalized)

    @field_validator("payment_provider")
    @classmethod
    def normalize_payment_provider(cls, value):
        value = value.strip()

        if not value:
            raise ValueError(
                "Payment provider tidak boleh kosong."
            )

        return value


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    payment_method: str | None = None
    payment_provider: str | None = None
    amount: float
    payment_status: str
    transaction_id: str | None = None
    created_at: datetime | None = None
    paid_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def order_deadline(order: Order) -> datetime | None:
    expires_at = getattr(order, "expires_at", None)

    if expires_at is not None:
        return as_utc(expires_at)

    created_at = getattr(order, "created_at", None)

    if created_at is None:
        return None

    created_at = as_utc(created_at)

    if created_at is None:
        return None

    return created_at + timedelta(
        seconds=EXPIRATION_SECONDS
    )


def order_is_expired(order: Order) -> bool:
    deadline = order_deadline(order)

    return deadline is not None and utc_now() >= deadline


# ============================================================
# RESPONSE HELPER
# ============================================================

def payment_to_response(
    payment: Payment,
    payment_provider: str | None = None,
) -> dict:
    """
    Model Payment menggunakan:
    - method
    - status
    - transaction_id
    - created_at
    - updated_at

    payment_provider dan paid_at belum memiliki kolom
    tersendiri di model yang digunakan saat ini.
    """
    return {
        "id": payment.id,
        "order_id": payment.order_id,
        "payment_method": payment.method,
        "payment_provider": payment_provider,
        "amount": float(payment.amount),
        "payment_status": payment.status,
        "transaction_id": payment.transaction_id,
        "created_at": payment.created_at,
        "paid_at": None,
    }


# ============================================================
# EXPIRE ORDER
# ============================================================

def fail_expired_order(
    db: Session,
    order: Order,
) -> bool:
    """
    Batalkan pesanan pending yang kedaluwarsa.

    Stok hanya dikembalikan apabila stock_reserved=True.
    Pemanggil bertanggung jawab melakukan commit.
    """
    if order.status != "pending":
        return False

    if not order_is_expired(order):
        return False

    if getattr(order, "stock_reserved", False):
        restore_order_stock(db, order)
        order.stock_reserved = False

    order.status = "cancelled"

    payments = db.scalars(
        select(Payment)
        .where(
            Payment.order_id == order.id,
            Payment.status == "pending",
        )
        .with_for_update()
    ).all()

    for payment in payments:
        payment.status = "failed"

    return True


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_order_for_update(
    db: Session,
    order_id: int,
) -> Order | None:
    return db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
    )


def get_payment_for_update(
    db: Session,
    payment_id: int,
) -> Payment | None:
    return db.scalar(
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
    )


# ============================================================
# CREATE PAYMENT
# ============================================================

@router.post(
    "/",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        order = get_order_for_update(
            db,
            payload.order_id,
        )

        if order is None or order.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan.",
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Pesanan tidak dapat dibayar. "
                    f"Status pesanan: {order.status}."
                ),
            )

        if order_is_expired(order):
            fail_expired_order(db, order)
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Waktu pembayaran habis. "
                    "Pesanan telah dibatalkan. "
                    "Silakan pesan tiket kembali."
                ),
            )

        existing_payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.status == "pending",
            )
            .with_for_update()
        )

        if existing_payment is not None:
            result = payment_to_response(
                existing_payment,
                payload.payment_provider,
            )

            db.commit()
            return result

        payment = Payment(
            order_id=order.id,
            method=payload.payment_method,
            amount=order.total_price,
            status="pending",
        )

        db.add(payment)
        db.flush()

        result = payment_to_response(
            payment,
            payload.payment_provider,
        )

        db.commit()
        db.refresh(payment)

        return result

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat membuat pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal membuat pembayaran.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan tidak terduga saat membuat pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membuat pembayaran.",
        )


# ============================================================
# CONFIRM PAYMENT
# ============================================================

@router.post("/{payment_id}/confirm")
def confirm_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Konfirmasi pembayaran simulasi.

    Endpoint ini bukan verifikasi dari payment gateway sungguhan.
    """
    try:
        initial_payment = db.get(
            Payment,
            payment_id,
        )

        if initial_payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan.",
            )

        # Kunci pesanan sebelum mengunci pembayaran.
        order = get_order_for_update(
            db,
            initial_payment.order_id,
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan.",
            )

        if order.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak memiliki akses ke pembayaran ini.",
            )

        payment = get_payment_for_update(
            db,
            payment_id,
        )

        if payment is None or payment.order_id != order.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan untuk pesanan ini.",
            )

        if payment.status == "success" and order.status == "paid":
            return {
                "message": "Pembayaran sudah berhasil.",
                "payment_id": payment.id,
                "payment_status": payment.status,
                "order_id": order.id,
                "order_status": order.status,
            }

        if payment.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Pembayaran tidak dapat dikonfirmasi. "
                    f"Status pembayaran: {payment.status}."
                ),
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Pesanan tidak dapat dibayar. "
                    f"Status pesanan: {order.status}."
                ),
            )

        if order_is_expired(order):
            fail_expired_order(db, order)
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Waktu pembayaran habis. "
                    "Pesanan telah dibatalkan."
                ),
            )

        # Stok sudah direservasi saat order dibuat.
        # Jangan kurangi stok untuk kedua kalinya.
        payment.status = "success"
        order.status = "paid"

        # Reservasi selesai, tetapi stok tetap terpakai.
        order.stock_reserved = False

        db.commit()

        db.refresh(payment)
        db.refresh(order)

        return {
            "message": "Pembayaran berhasil dikonfirmasi.",
            "payment_id": payment.id,
            "payment_status": payment.status,
            "order_id": order.id,
            "order_status": order.status,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengonfirmasi pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengonfirmasi pembayaran.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan tidak terduga saat mengonfirmasi pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat mengonfirmasi pembayaran.",
        )


# ============================================================
# GET MY PAYMENTS
# ============================================================

@router.get(
    "/my",
    response_model=list[PaymentResponse],
)
def get_my_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        payments = db.scalars(
            select(Payment)
            .join(
                Order,
                Payment.order_id == Order.id,
            )
            .where(
                Order.user_id == current_user.id,
            )
            .order_by(Payment.id.desc())
        ).all()

        # Provider tidak dapat dipulihkan dari database karena
        # model Payment belum memiliki kolom payment_provider.
        return [
            payment_to_response(payment)
            for payment in payments
        ]

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil daftar pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pembayaran gagal dimuat.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat mengambil daftar pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat daftar pembayaran.",
        )
