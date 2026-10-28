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

EXPIRATION_SECONDS = 60


# ============================================================
# SCHEMAS
# ============================================================

class OrderCreate(BaseModel):
    event_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    ticket_class_id: int = Field(gt=0)


class OrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    ticket_class_id: int
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


class OrderDetailResponse(OrderHistoryResponse):
    payment: PaymentDetailResponse | None = None


# ============================================================
# AUTHORIZATION
# ============================================================

def require_user(current_user: User) -> None:
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fitur ini hanya tersedia untuk akun pengguna.",
        )


def require_admin(current_user: User) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin.",
        )


# ============================================================
# TIME / EXPIRATION
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

    created_at = as_utc(getattr(order, "created_at", None))

    if created_at is None:
        return None

    return created_at + timedelta(seconds=EXPIRATION_SECONDS)


def is_expired(order: Order) -> bool:
    deadline = order_deadline(order)
    return deadline is not None and utc_now() >= deadline


def expire_order_if_needed(
    db: Session,
    order: Order,
) -> bool:
    """
    Batalkan pesanan pending yang kedaluwarsa.

    Stok dikembalikan hanya jika stock_reserved=True.
    Pemanggil bertanggung jawab melakukan commit.
    """
    if order.status != "pending" or not is_expired(order):
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
# RESPONSE HELPERS
# ============================================================

def order_to_dict(order: Order) -> dict:
    event = getattr(order, "event", None)

    return {
        "id": order.id,
        "user_id": order.user_id,
        "event_id": order.event_id,
        "ticket_class_id": order.ticket_class_id,
        "quantity": order.quantity,
        "total_price": order.total_price,
        "status": order.status,
        "created_at": order.created_at,
        "expires_at": order_deadline(order),
        "event_title": (
            event.title if event is not None else "Event tidak tersedia"
        ),
    }


def payment_to_dict(payment: Payment | None) -> dict | None:
    if payment is None:
        return None

    return {
        "id": payment.id,
        "amount": payment.amount,
        "payment_method": payment.method,
        "payment_status": payment.status,
        "transaction_id": payment.transaction_id,
        "paid_at": payment.paid_at,
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
        event = db.scalar(
            select(Event)
            .where(Event.id == data.event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        if getattr(event, "is_cancelled", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event sudah dibatalkan dan tidak dapat dipesan.",
            )

        event_date = as_utc(event.event_date)

        if event_date is not None and event_date <= utc_now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event sudah berlangsung.",
            )

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
                detail="Kelas tiket tidak ditemukan untuk event ini.",
            )

        if ticket_class.available_tickets < data.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Stok kelas tiket tidak mencukupi. "
                    f"Sisa stok: {ticket_class.available_tickets}."
                ),
            )

        total_price = Decimal(str(ticket_class.price)) * data.quantity

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            ticket_class_id=ticket_class.id,
            quantity=data.quantity,
            total_price=total_price,
            status="pending",
            expires_at=utc_now() + timedelta(
                seconds=EXPIRATION_SECONDS
            ),
            stock_reserved=True,
        )

        ticket_class.available_tickets -= data.quantity

        db.add(order)
        db.flush()
        db.commit()
        db.refresh(order)

        return order

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat membuat pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal disimpan. Silakan coba lagi.",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membuat pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membuat pesanan.",
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

        return [order_to_dict(order) for order in orders]

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat mengambil riwayat pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Riwayat pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membaca riwayat pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat riwayat pesanan.",
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

        return [order_to_dict(order) for order in orders]

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat mengambil semua pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membaca semua pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat data pesanan.",
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
                detail="Pesanan tidak ditemukan atau bukan milik akun ini.",
            )

        if expire_order_if_needed(db, order):
            db.commit()
            db.refresh(order)

        payment = db.scalar(
            select(Payment)
            .where(Payment.order_id == order.id)
            .order_by(Payment.id.desc())
        )

        result = order_to_dict(order)
        result["payment"] = payment_to_dict(payment)

        return result

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat mengambil detail pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detail pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membaca detail pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat detail pesanan.",
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
                detail="Pesanan tidak ditemukan.",
            )

        if order.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hanya pesanan pending yang dapat dibatalkan.",
            )

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

        if getattr(order, "stock_reserved", False):
            restore_order_stock(db, order)
            order.stock_reserved = False

        order.status = "cancelled"

        db.commit()

        return {
            "message": "Pesanan berhasil dibatalkan.",
            "order_id": order.id,
            "status": order.status,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error saat membatalkan pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal dibatalkan.",
        )

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membatalkan pesanan.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membatalkan pesanan.",
        )