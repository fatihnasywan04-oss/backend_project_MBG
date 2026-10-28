from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Order, Payment
from routes.auth_routes import get_current_user
from order_stock import restore_order_stock

router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)

# Harus sama dengan batas waktu di routes/order_routes.py.
EXPIRATION_SECONDS = 60


class PaymentCreate(BaseModel):
    order_id: int
    payment_method: Literal[
        "transfer_bank",
        "e_wallet",
        "qris",
    ]
    payment_provider: str

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


def utc_now() -> datetime:
    """Menghasilkan waktu UTC."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """
    Mengubah datetime menjadi UTC.

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
    Menggunakan expires_at dari pesanan.

    Jika expires_at belum tersedia, batas waktu dihitung
    dari created_at + EXPIRATION_SECONDS detik.
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

    return created_at_utc + timedelta(seconds=EXPIRATION_SECONDS)


def order_is_expired(order: Order) -> bool:
    deadline = order_deadline(order)

    if deadline is None:
        return False

    return utc_now() >= deadline


def fail_expired_order(db: Session, order: Order) -> None:
    """Batalkan pesanan kedaluwarsa dan gagalkan pembayaran pending.

    Order baru mereservasi stok ketika dibuat, sehingga stok perlu
    dikembalikan dalam transaksi yang sama saat order kedaluwarsa.
    Order lama tanpa expires_at diperlakukan sebagai order lama dan
    tidak otomatis mengembalikan stok karena status reservasinya tidak diketahui.
    """
    if order.status == "pending":
        # expires_at ditetapkan oleh endpoint create_order yang juga
        # mengurangi stok. Untuk order lama tanpa expires_at, jangan
        # menambah stok secara spekulatif.
        if getattr(order, "expires_at", None) is not None:
            restore_order_stock(db, order)
        order.status = "cancelled"

    payments = db.scalars(
        select(Payment)
        .where(Payment.order_id == order.id)
        .with_for_update()
    ).all()

    for payment in payments:
        if payment.payment_status == "pending":
            payment.payment_status = "failed"


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


@router.post(
    "/",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Membuat pembayaran untuk pesanan yang masih pending.

    Membuat pembayaran tidak mengurangi stok tiket.
    """
    try:
        order = db.scalar(
            select(Order)
            .where(
                Order.id == payload.order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if order is None:
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
                detail="Waktu pembayaran habis. Pesanan telah dibatalkan.",
            )

        # Jika sudah ada pembayaran pending untuk pesanan ini,
        # gunakan pembayaran tersebut agar tidak membuat duplikat.
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

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal membuat pembayaran.",
        )


@router.post("/{payment_id}/confirm")
def confirm_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Mengonfirmasi pembayaran simulasi.

    Stok sudah direservasi saat order dibuat, jadi endpoint ini hanya
    mengubah status pembayaran dan order. Konfirmasi berulang tidak
    akan mengurangi stok lagi.
    """
    try:
        # Ambil pembayaran untuk mengetahui order_id.
        initial_payment = db.scalar(
            select(Payment).where(Payment.id == payment_id)
        )

        if initial_payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan.",
            )

        # Kunci pesanan terlebih dahulu agar konfirmasi yang
        # bersamaan untuk satu pesanan tidak memproses stok ganda.
        order = db.scalar(
            select(Order)
            .where(Order.id == initial_payment.order_id)
            .with_for_update()
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

        payment = db.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        )

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan.",
            )

        # Jika pembayaran sudah sukses, jangan kurangi stok lagi.
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

        # Pastikan tenggat belum terlewati.
        if order_is_expired(order):
            fail_expired_order(db, order)
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Waktu pembayaran habis. Pesanan telah dibatalkan.",
            )

        # Stok sudah dikurangi saat order dibuat. Di sini hanya
        # ubah status pembayaran dan pesanan dalam satu transaksi.
        payment.payment_status = "success"
        payment.paid_at = utc_now()
        order.status = "paid"

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

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengonfirmasi pembayaran.",
        )


@router.get("/my")
def get_my_payments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Mengambil daftar pembayaran milik pengguna yang login.
    """
    payments = db.scalars(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(Order.user_id == current_user.id)
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
            "created_at": getattr(payment, "created_at", None),
            "paid_at": getattr(payment, "paid_at", None),
        }
        for payment in payments
    ]