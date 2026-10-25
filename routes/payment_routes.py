
import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, User
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
        aliases = {
            "bank_transfer": "transfer_bank",
            "ewallet": "e_wallet",
            "e-wallet": "e_wallet",
            "QRIS": "qris",
        }
        if isinstance(value, str):
            value = aliases.get(value, value)
        return value


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    amount: Decimal
    payment_method: str
    payment_status: str
    transaction_id: str | None
    paid_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


def require_user(current_user: User) -> None:
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fitur ini hanya tersedia untuk akun user",
        )


def make_payment_code(method: str) -> str:
    prefixes = {
        "transfer_bank": "VA",
        "e_wallet": "EW",
        "qris": "QR",
    }
    return f"SIM-{prefixes[method]}-{secrets.token_hex(5).upper()}"


# POST /payments/
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
    require_user(current_user)

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
                detail="Pesanan tidak lagi menunggu pembayaran",
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

            # Gunakan kembali pembayaran pending yang sudah dibuat.
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

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat membuat pembayaran")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pembayaran gagal dibuat",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membuat pembayaran")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membuat pembayaran",
        )


# POST /payments/{payment_id}/confirm
# SIMULASI konfirmasi pembayaran, bukan verifikasi bank sungguhan.
@router.post(
    "/{payment_id}/confirm",
    response_model=PaymentResponse,
)
def confirm_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        # Kunci payment.
        payment = db.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        )

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan",
            )

        # Kunci order milik user.
        order = db.scalar(
            select(Order)
            .where(
                Order.id == payment.order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan",
            )

        # Idempotensi: pembayaran sukses tidak boleh mengurangi stok lagi.
        if payment.payment_status == "success":
            return payment

        if payment.payment_status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pembayaran tidak berstatus pending",
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pesanan tidak lagi menunggu pembayaran",
            )

        # Kurangi stok secara atomik hanya jika stok terbaru mencukupi.
        # Jika stok kurang, tidak ada perubahan stok.
        result = db.execute(
            update(Event)
            .where(
                Event.id == order.event_id,
                Event.available_tickets >= order.quantity,
            )
            .values(
                available_tickets=(
                    Event.available_tickets - order.quantity
                )
            )
        )

        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Stok tiket sudah tidak mencukupi. "
                    "Pembayaran tidak dapat dikonfirmasi. "
                    "Silakan pilih event atau jumlah tiket lain."
                ),
            )

        # Perubahan stok, payment, dan order disimpan dalam
        # satu transaksi yang sama.
        payment.payment_status = "success"
        payment.paid_at = datetime.now(timezone.utc)
        order.status = "paid"

        db.commit()
        db.refresh(payment)

        return payment

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat konfirmasi pembayaran")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Konfirmasi pembayaran gagal diproses",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat konfirmasi pembayaran")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat konfirmasi pembayaran",
        )


# GET /payments/
@router.get(
    "/",
    response_model=list[PaymentResponse],
)
def get_my_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    payments = db.scalars(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(Order.user_id == current_user.id)
        .order_by(Payment.id.desc())
    ).all()

    return payments
