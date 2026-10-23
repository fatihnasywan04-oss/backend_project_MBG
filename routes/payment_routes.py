
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Order, Payment, User
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)

ALLOWED_METHODS = {
    "transfer_bank",
    "e_wallet",
    "qris",
}


class PaymentCreate(BaseModel):
    order_id: int
    payment_method: str

    @field_validator("payment_method", mode="before")
    @classmethod
    def normalize_payment_method(cls, value):
        if not isinstance(value, str):
            raise ValueError("Metode pembayaran harus berupa teks")

        method = value.strip().lower()
        method = method.replace("-", "_").replace(" ", "_")

        aliases = {
            "qris_payment": "qris",
            "qr_is": "qris",
            "ewallet": "e_wallet",
            "e_wallet_payment": "e_wallet",
            "bank_transfer": "transfer_bank",
        }

        method = aliases.get(method, method)

        if method not in ALLOWED_METHODS:
            raise ValueError(
                "Metode tidak valid. Gunakan transfer_bank, e_wallet, atau qris."
            )

        return method


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    amount: float
    payment_method: str
    payment_status: str
    transaction_id: str | None
    paid_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


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
                detail=f"Pesanan tidak dapat dibayar. Status saat ini: {order.status}",
            )

        existing_payment = db.scalar(
            select(Payment).where(
                Payment.order_id == order.id
            )
        )

        if existing_payment is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Pembayaran untuk pesanan ini sudah pernah dibuat. "
                    f"Status pembayaran: {existing_payment.payment_status}"
                ),
            )

        payment = Payment(
            order_id=order.id,
            amount=order.total_price,
            payment_method=data.payment_method,
            payment_status="success",  # SIMULASI SAJA
            transaction_id=f"SIM-{uuid.uuid4().hex[:12].upper()}",
            paid_at=datetime.now(timezone.utc),
        )

        order.status = "paid"

        db.add(payment)
        db.commit()
        db.refresh(payment)

        return payment

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logger.exception("Gagal menyimpan pembayaran untuk order %s", data.order_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pembayaran gagal disimpan. Periksa log backend.",
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

    payments = db.scalars(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(Order.user_id == current_user.id)
        .order_by(Payment.id.desc())
    ).all()

    return payments
