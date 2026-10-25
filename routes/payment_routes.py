
import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Order, Payment, User
from routes.auth_routes import get_current_user


router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)

PaymentMethod = Literal["transfer_bank", "e_wallet", "qris"]


class PaymentCreate(BaseModel):
    order_id: int
    payment_method: PaymentMethod

    @field_validator("payment_method", mode="before")
    @classmethod
    def normalize_payment_method(cls, value):
        if not isinstance(value, str):
            raise ValueError("Metode pembayaran harus berupa teks")

        method = value.strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "qris_payment": "qris",
            "qr_is": "qris",
            "ewallet": "e_wallet",
            "e_wallet_payment": "e_wallet",
            "bank_transfer": "transfer_bank",
        }

        return aliases.get(method, method)


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    amount: Decimal
    payment_method: str
    payment_status: str
    transaction_id: str | None
    paid_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


def make_payment_code(method: str) -> str:
    # Kode untuk tampilan instruksi pembayaran.
    # Kode ini tidak terhubung ke bank atau penyedia pembayaran.
    if method == "qris":
        return f"QR-{secrets.token_hex(8).upper()}"
    if method == "e_wallet":
        return f"EW-{secrets.randbelow(10**16):016d}"
    return f"VA-{secrets.randbelow(10**16):016d}"


@router.post(
    "/",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pembayaran hanya tersedia untuk akun user",
        )

    try:
        order = db.scalar(
            select(Order)
            .where(
                Order.id == data.order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan",
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pesanan ini tidak dapat dibayar lagi",
            )

        existing_payment = db.scalar(
            select(Payment)
            .where(Payment.order_id == order.id)
            .with_for_update()
        )

        if existing_payment is not None:
            if existing_payment.payment_status == "success":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Pesanan ini sudah dibayar",
                )

            # Jika pembayaran pending sudah ada, gunakan kembali.
            # Metode pembayaran tetap mengikuti pilihan awal.
            return existing_payment

        payment = Payment(
            order_id=order.id,
            amount=order.total_price,
            payment_method=data.payment_method,
            payment_status="pending",
            transaction_id=make_payment_code(data.payment_method),
            paid_at=None,
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
        logger.exception(
            "Gagal membuat pembayaran untuk pesanan %s",
            data.order_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pembayaran gagal dibuat",
        )


@router.post(
    "/{payment_id}/confirm",
    response_model=PaymentResponse,
)
def confirm_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konfirmasi pembayaran hanya tersedia untuk akun user",
        )

    try:
        payment = db.scalar(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(
                Payment.id == payment_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan",
            )

        order = db.scalar(
            select(Order)
            .where(Order.id == payment.order_id)
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan",
            )

        if payment.payment_status == "success":
            return payment

        if payment.payment_status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Status pembayaran tidak dapat dikonfirmasi",
            )

        # Konfirmasi ini menyelesaikan alur demo aplikasi,
        # bukan verifikasi pembayaran dari bank atau penyedia QRIS.
        payment.payment_status = "success"
        payment.paid_at = datetime.now(timezone.utc)
        order.status = "paid"

        db.commit()
        db.refresh(payment)
        return payment

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception(
            "Gagal mengonfirmasi pembayaran %s",
            payment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pembayaran gagal dikonfirmasi",
        )


@router.get(
    "/",
    response_model=list[PaymentResponse],
)
def get_my_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Riwayat pembayaran hanya tersedia untuk akun user",
        )

    return db.scalars(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(Order.user_id == current_user.id)
        .order_by(Payment.id.desc())
    ).all()
