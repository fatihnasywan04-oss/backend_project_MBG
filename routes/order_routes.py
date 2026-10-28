import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, TicketClass, User
from routes.auth_routes import get_current_user
from order_stock import restore_order_stock

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)

# Batas pembayaran: 30 detik
EXPIRATION_SECONDS = 60


# ============================================================
# SCHEMAS
# ============================================================

class OrderCreate(BaseModel):
    event_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    ticket_class_id: int | None = Field(default=None, gt=0)


class OrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    ticket_class_id: int | None = None
    quantity: int
    total_price: Decimal
    status: str
    created_at: datetime
    expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OrderHistoryResponse(OrderResponse):
    event_title: str


class PaymentDetailResponse(BaseModel):
    id: int
    amount: Decimal
    payment_method: str | None = None
    payment_status: str
    transaction_id: str | None = None
    paid_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OrderDetailResponse(OrderHistoryResponse):
    payment: PaymentDetailResponse | None = None


# ============================================================
# AUTHORIZATION
# ============================================================

def require_user(current_user: User) -> None:
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fitur ini hanya tersedia untuk akun user",
        )


def require_admin(current_user: User) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )


# ============================================================
# TIME / EXPIRATION
# ============================================================

def as_utc(value: datetime) -> datetime:
    """
    Normalisasi datetime ke UTC.
    Database lama mungkin mengembalikan datetime tanpa timezone.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def order_deadline(order: Order) -> datetime:
    """
    Mengambil batas waktu pembayaran.

    Order baru menggunakan expires_at.
    Order lama yang belum memiliki expires_at menggunakan
    created_at + 30 detik.
    """
    if order.expires_at is not None:
        return as_utc(order.expires_at)

    return as_utc(order.created_at) + timedelta(
        seconds=EXPIRATION_SECONDS
    )


def is_expired(order: Order) -> bool:
    return datetime.now(timezone.utc) >= order_deadline(order)


def expire_order_if_needed(
    db: Session,
    order: Order,
) -> bool:
    """
    Membatalkan order pending yang sudah melewati batas waktu.

    Order yang sudah paid atau cancelled tidak diubah.

    Stok tidak perlu dikembalikan karena stok baru dikurangi
    saat pembayaran berhasil dikonfirmasi.
    """
    if order.status != "pending":
        return False

    if not is_expired(order):
        return False

    order.status = "cancelled"

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
# RESPONSE HELPER
# ============================================================

def order_to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "event_id": order.event_id,
        "ticket_class_id": order.ticket_class_id,
        "event_title": order.event.title,
        "quantity": order.quantity,
        "total_price": order.total_price,
        "status": order.status,
        "created_at": order.created_at,
        "expires_at": order_deadline(order),
    }


# ============================================================
# CREATE ORDER
# ============================================================

@router.post(
    "/",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(
    data: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        # Kunci event untuk mengurangi risiko transaksi bersamaan.
        event = db.scalar(
            select(Event)
            .where(Event.id == data.event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan",
            )

        if getattr(event, "is_cancelled", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event sudah dibatalkan dan tidak dapat dipesan",
            )

        if as_utc(event.event_date) <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event sudah berlangsung",
            )

        ticket_class = None

        # Jika user memilih kelas tiket.
        if data.ticket_class_id is not None:
            ticket_class = db.scalar(
                select(TicketClass)
                .where(
                    TicketClass.id == data.ticket_class_id,
                    TicketClass.event_id == event.id,
                )
                .with_for_update()
            )

            if ticket_class is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Kelas tiket tidak ditemukan untuk event ini",
                )

            if ticket_class.available_tickets < data.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Stok kelas tiket tidak mencukupi. "
                        f"Sisa stok: {ticket_class.available_tickets}"
                    ),
                )

            unit_price = Decimal(str(ticket_class.price))

        else:
            # Kompatibilitas dengan pemesanan event lama tanpa kelas.
            if event.available_tickets < data.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Stok tiket tidak mencukupi. "
                        f"Sisa stok: {event.available_tickets}"
                    ),
                )

            unit_price = Decimal(str(event.price))

        now = datetime.now(timezone.utc)

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            ticket_class_id=(
                ticket_class.id if ticket_class is not None else None
            ),
            quantity=data.quantity,
            total_price=unit_price * data.quantity,
            status="pending",
            expires_at=now + timedelta(
                seconds=EXPIRATION_SECONDS
            ),
        )

        # Stok tidak dikurangi saat order dibuat.
        # Stok hanya dikurangi ketika pembayaran berhasil.
        db.add(order)
        db.commit()
        db.refresh(order)

        return order

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat membuat pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal disimpan. Silakan coba lagi.",
        )


# ============================================================
# GET MY ORDERS
# ============================================================

@router.get(
    "/",
    response_model=list[OrderHistoryResponse],
)
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        orders = db.scalars(
            select(Order)
            .options(joinedload(Order.event))
            .where(Order.user_id == current_user.id)
            .order_by(Order.created_at.desc())
            .with_for_update(of=Order)
        ).unique().all()

        changed = False

        for order in orders:
            if expire_order_if_needed(db, order):
                changed = True

        if changed:
            db.commit()

        return [
            order_to_dict(order)
            for order in orders
        ]

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil riwayat pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Riwayat pesanan gagal dimuat",
        )


# ============================================================
# GET ALL ORDERS (ADMIN)
# ============================================================

@router.get(
    "/admin/all",
    response_model=list[OrderHistoryResponse],
)
def get_all_orders_admin(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    try:
        orders = db.scalars(
            select(Order)
            .options(joinedload(Order.event))
            .order_by(Order.created_at.desc())
            .with_for_update(of=Order)
        ).unique().all()

        changed = False

        for order in orders:
            if expire_order_if_needed(db, order):
                changed = True

        if changed:
            db.commit()

        return [
            order_to_dict(order)
            for order in orders
        ]

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil seluruh pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data pesanan gagal dimuat",
        )


# ============================================================
# GET ORDER DETAIL
# ============================================================

@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
)
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        order = db.scalar(
            select(Order)
            .options(joinedload(Order.event))
            .where(
                Order.id == order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update(of=Order)
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan atau bukan milik akun ini",
            )

        changed = expire_order_if_needed(db, order)

        if changed:
            db.commit()
            db.refresh(order)

        payment = db.scalar(
            select(Payment)
            .where(Payment.order_id == order.id)
            .order_by(Payment.id.desc())
        )

        result = order_to_dict(order)

        result["payment"] = (
            {
                "id": payment.id,
                "amount": payment.amount,
                "payment_method": payment.payment_method,
                "payment_status": payment.payment_status,
                "transaction_id": payment.transaction_id,
                "paid_at": payment.paid_at,
            }
            if payment is not None
            else None
        )

        return result

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil detail pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detail pesanan gagal dimuat",
        )


# ============================================================
# CANCEL ORDER
# ============================================================

@router.delete("/{order_id}")
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    try:
        order = db.scalar(
            select(Order)
            .where(
                Order.id == order_id,
                Order.user_id == current_user.id,
            )
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan",
            )

        if expire_order_if_needed(db, order):
            db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Waktu pembayaran habis. Pesanan telah kedaluwarsa.",
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hanya pesanan pending yang dapat dibatalkan",
            )

        order.status = "cancelled"

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

        # Stok tidak dikembalikan karena belum dikurangi.
        db.commit()

        return {
            "message": "Pesanan berhasil dibatalkan",
            "order_id": order.id,
            "status": "cancelled",
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat membatalkan pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal dibatalkan",
        )