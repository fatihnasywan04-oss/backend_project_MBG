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

# Harus sama dengan EXPIRATION_SECONDS di order_routes.py.
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

    payment_provider: str = Field(min_length=1, max_length=100)

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
            raise ValueError("Payment provider tidak boleh kosong.")

        return value


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    payment_method: str
    payment_provider: str
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
    """Menghasilkan waktu UTC."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """
    Normalisasi datetime ke UTC.

    Jika database menyimpan datetime tanpa timezone,
    nilainya diasumsikan sudah menggunakan UTC.
    """
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def order_deadline(order: Order) -> datetime | None:
    """
    Mengambil batas waktu pembayaran.

    Menggunakan expires_at jika tersedia.
    Untuk pesanan lama tanpa expires_at, gunakan
    created_at + EXPIRATION_SECONDS.
    """
    expires_at = getattr(order, "expires_at", None)

    if expires_at is not None:
        return as_utc(expires_at)

    created_at = getattr(order, "created_at", None)

    if created_at is None:
        return None

    created_at_utc = as_utc(created_at)

    if created_at_utc is None:
        return None

    return created_at_utc + timedelta(
        seconds=EXPIRATION_SECONDS
    )


def order_is_expired(order: Order) -> bool:
    deadline = order_deadline(order)

    if deadline is None:
        return False

    return utc_now() >= deadline


# ============================================================
# ORDER EXPIRATION
# ============================================================

def fail_expired_order(
    db: Session,
    order: Order,
) -> bool:
    """
    Membatalkan pesanan pending yang sudah kedaluwarsa.

    Stok dikembalikan hanya jika stock_reserved=True.
    Flag diubah menjadi False agar stok tidak dikembalikan
    lebih dari satu kali.

    Pemanggil bertanggung jawab melakukan commit.
    """
    if order.status != "pending":
        return False

    if not order_is_expired(order):
        return False

    # Kembalikan stok hanya jika pesanan masih memiliki reservasi.
    if getattr(order, "stock_reserved", False):
        restore_order_stock(db, order)
        order.stock_reserved = False

    order.status = "cancelled"

    # Tandai pembayaran pending sebagai gagal.
    payments = db.scalars(
        select(Payment)
        .where(
            Payment.order_id == order.id,
            Payment.payment_status == "pending",
        )
        .with_for_update()
    ).all()

    for payment in payments:
        payment.payment_status = "failed"

    return True


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_order_for_update(
    db: Session,
    order_id: int,
) -> Order | None:
    """
    Mengunci pesanan selama transaksi untuk mengurangi
    risiko dua proses mengubah pesanan secara bersamaan.
    """
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
    """
    Membuat pembayaran untuk pesanan pending.

    Penting:
    Endpoint ini tidak mengurangi stok.
    Stok sudah direservasi ketika pesanan dibuat.
    """
    try:
        # Kunci pesanan sebelum memeriksa status dan pembayaran.
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

        # Periksa apakah pesanan sudah kedaluwarsa.
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

        # Hindari membuat pembayaran pending berulang kali
        # untuk pesanan yang sama.
        existing_payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.payment_status == "pending",
            )
            .with_for_update()
        )

        if existing_payment is not None:
            return existing_payment

        payment = Payment(
            order_id=order.id,
            payment_method=payload.payment_method,
            payment_provider=payload.payment_provider,
            amount=order.total_price,
            payment_status="pending",
        )

        db.add(payment)
        db.commit()
        db.refresh(payment)

        return payment

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
    Mengonfirmasi pembayaran simulasi.

    Jika pembayaran berhasil:
    - payment_status menjadi success;
    - paid_at diisi;
    - status pesanan menjadi paid;
    - stok TIDAK dikurangi lagi.

    Pengurangan stok sudah dilakukan saat order dibuat.
    """
    try:
        # Cari pembayaran terlebih dahulu untuk mengetahui order_id.
        initial_payment = db.get(Payment, payment_id)

        if initial_payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan.",
            )

        # Kunci pesanan terlebih dahulu agar konfirmasi bersamaan
        # untuk pesanan yang sama tidak memprosesnya dua kali.
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

        # Setelah order terkunci, kunci pembayaran.
        payment = get_payment_for_update(
            db,
            payment_id,
        )

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan.",
            )

        # Konfirmasi berulang tidak boleh memproses ulang pembayaran.
        if payment.payment_status == "success":
            return {
                "message": "Pembayaran sudah berhasil.",
                "payment_id": payment.id,
                "payment_status": payment.payment_status,
                "order_id": order.id,
                "order_status": order.status,
            }

        if payment.payment_status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Pembayaran tidak dapat dikonfirmasi. "
                    f"Status pembayaran: {payment.payment_status}."
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

        # Jika sudah kedaluwarsa, batalkan pesanan dan
        # kembalikan reservasi stok dalam transaksi yang sama.
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

        # Tidak ada pengurangan stok di endpoint ini.
        # Stok sudah dikurangi ketika pesanan dibuat.
        #
        # Perubahan status pembayaran dan pesanan disimpan
        # dalam satu transaksi.
        payment.payment_status = "success"
        payment.paid_at = utc_now()
        order.status = "paid"

        # Pesanan yang berhasil dibayar tidak lagi memegang
        # reservasi untuk dikembalikan saat kedaluwarsa.
        #
        # Stoknya tetap terpakai karena tiket telah terjual.
        order.stock_reserved = False

        db.commit()

        db.refresh(payment)
        db.refresh(order)

        return {
            "message": "Pembayaran berhasil dikonfirmasi.",
            "payment_id": payment.id,
            "payment_status": payment.payment_status,
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

@router.get("/my")
def get_my_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mengambil daftar pembayaran milik pengguna yang login.
    """
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

        return [
            {
                "id": payment.id,
                "order_id": payment.order_id,
                "payment_method": payment.payment_method,
                "payment_provider": payment.payment_provider,
                "amount": payment.amount,
                "payment_status": payment.payment_status,
                "transaction_id": getattr(
                    payment,
                    "transaction_id",
                    None,
                ),
                "created_at": getattr(
                    payment,
                    "created_at",
                    None,
                ),
                "paid_at": getattr(
                    payment,
                    "paid_at",
                    None,
                ),
            }
            for payment in payments
        ]

    except SQLAlchemyError:
        logger.exception(
            "Database error saat mengambil daftar pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pembayaran gagal dimuat.",
        )