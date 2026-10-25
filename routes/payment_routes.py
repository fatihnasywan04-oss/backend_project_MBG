
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Order, Payment, User
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)

EXPIRATION_MINUTES = 15

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
    amount: float
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


def order_is_expired(order: Order) -> bool:
    created_at = order.created_at

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) >= (
        created_at + timedelta(minutes=EXPIRATION_MINUTES)
    )


def fail_expired_order(
    db: Session,
    order: Order,
    payment: Payment | None = None,
) -> None:
    order.status = "cancelled"

    if payment is not None and payment.payment_status == "pending":
        payment.payment_status = "failed"

    other_payments = db.scalars(
        select(Payment)
        .where(
            Payment.order_id == order.id,
            Payment.payment_status == "pending",
        )
        .with_for_update()
    ).all()

    for item in other_payments:
        item.payment_status = "failed"


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

        if order_is_expired(order):
            fail_expired_order(db, order)
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pesanan sudah kedaluwarsa. Silakan buat pesanan baru.",
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

            if existing_payment.payment_status == "pending":
                # Gunakan kembali pembayaran pending yang sudah dibuat.
                return existing_payment

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pembayaran sebelumnya gagal. Buat pesanan baru.",
            )

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
        # Kunci order terlebih dahulu agar konfirmasi tidak berlomba
        # dengan pembatalan order pada transaksi lain.
        payment_ref = db.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
        )

        if payment_ref is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan",
            )

        order = db.scalar(
            select(Order)
            .where(
                Order.id == payment_ref.order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan",
            )

        payment = db.scalar(
            select(Payment)
            .where(
                Payment.id == payment_id,
                Payment.order_id == order.id,
            )
            .with_for_update()
        )

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pembayaran tidak ditemukan",
            )

        # Idempotensi: jangan kurangi stok dua kali.
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

        if order_is_expired(order):
            fail_expired_order(db, order, payment)
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pesanan sudah kedaluwarsa. Silakan buat pesanan baru.",
            )

        # Kurangi stok secara atomik hanya jika stok mencukupi.
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
                    "Pembayaran tidak dapat dikonfirmasi."
                ),
            )

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


@router.get(
    "/",
    response_model=list[PaymentResponse],
)
def get_my_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        return db.scalars(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(Order.user_id == current_user.id)
            .order_by(Payment.id.desc())
        ).all()

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil riwayat pembayaran")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Riwayat pembayaran gagal dimuat",
        )
