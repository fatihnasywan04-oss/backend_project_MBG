from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Order, Payment, TicketClass
from routes.auth_routes import get_current_user


router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)

# Harus sama dengan batas waktu di routes/orders.py.
EXPIRATION_SECONDS = 30


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
    dari created_at + 30 detik.
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
    """
    Membatalkan pesanan yang sudah melewati tenggat.

    Stok tidak dikurangi atau ditambah karena stok
    belum dikurangi ketika pesanan dibuat.
    """
    if order.status == "pending":
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

    Stok dikurangi hanya jika:
    - Pembayaran masih pending.
    - Pesanan masih pending.
    - Pesanan belum kedaluwarsa.
    - Stok tiket masih mencukupi.

    Pembayaran yang sudah success tidak memproses stok lagi.
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

        # Kunci event terkait.
        event = db.scalar(
            select(Event)
            .where(Event.id == order.event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        # Kurangi stok pada TicketClass jika pesanan menggunakan
        # kelas tiket. Jika tidak, gunakan stok pada Event.
        ticket_class_id = getattr(order, "ticket_class_id", None)
        quantity = int(order.quantity)

        if quantity <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jumlah tiket pada pesanan tidak valid.",
            )

        if ticket_class_id is not None:
            ticket_class = db.scalar(
                select(TicketClass)
                .where(
                    TicketClass.id == ticket_class_id,
                    TicketClass.event_id == event.id,
                )
                .with_for_update()
            )

            if ticket_class is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Kelas tiket tidak ditemukan.",
                )

            # Update bersyarat mencegah stok menjadi negatif.
            result = db.execute(
                update(TicketClass)
                .where(
                    TicketClass.id == ticket_class.id,
                    TicketClass.available_tickets >= quantity,
                )
                .values(
                    available_tickets=(
                        TicketClass.available_tickets - quantity
                    )
                )
            )

            if result.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Stok tiket tidak mencukupi.",
                )

        else:
            # Kompatibilitas dengan model lama yang menyimpan
            # stok langsung pada Event.
            result = db.execute(
                update(Event)
                .where(
                    Event.id == event.id,
                    Event.available_tickets >= quantity,
                )
                .values(
                    available_tickets=(
                        Event.available_tickets - quantity
                    )
                )
            )

            if result.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Stok tiket tidak mencukupi.",
                )

        # Update status pembayaran dan pesanan dalam transaksi
        # yang sama dengan pengurangan stok.
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