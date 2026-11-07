
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, TicketClass, User
from routes.auth_routes import get_current_user
from order_stock import restore_order_stock


router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)

logger = logging.getLogger(__name__)

# Masa berlaku pesanan dalam detik.
EXPIRATION_SECONDS = 60


# ============================================================
# SCHEMAS
# ============================================================

class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    ticket_class_id: int = Field(gt=0)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: int):
        if isinstance(value, bool):
            raise ValueError("Jumlah tiket harus berupa bilangan bulat.")

        if value <= 0:
            raise ValueError("Jumlah tiket harus lebih dari 0.")

        return value


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


class UserSummary(BaseModel):
    id: int
    name: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class OrderHistoryResponse(OrderResponse):
    event_title: str
    user: UserSummary | None = None


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
            detail="Akses hanya untuk Super Admin.",
        )


def require_approved_event_admin(current_user: User) -> None:
    if (
        current_user.role != "admin_event"
        or current_user.admin_event_status != "approved"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk Admin Event yang sudah disetujui.",
        )


def require_transaction_admin(current_user: User) -> None:
    if current_user.role == "admin":
        return

    if current_user.role == "admin_event":
        require_approved_event_admin(current_user)
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Anda tidak memiliki akses untuk melihat transaksi.",
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

    created_at = as_utc(
        getattr(order, "created_at", None)
    )

    if created_at is None:
        return None

    return created_at + timedelta(
        seconds=EXPIRATION_SECONDS
    )


def is_expired(order: Order) -> bool:
    deadline = order_deadline(order)

    return (
        deadline is not None
        and utc_now() >= deadline
    )


def expire_order_if_needed(
    db: Session,
    order: Order,
) -> bool:
    """
    Membatalkan order pending yang sudah kedaluwarsa.

    Stok dikembalikan hanya jika stock_reserved=True.
    Fungsi ini tidak melakukan commit.
    """

    if order.status != "pending":
        return False

    if not is_expired(order):
        return False

    if order.stock_reserved:
        restore_order_stock(db, order)

        # restore_order_stock juga mengubah stock_reserved
        # menjadi False. Penetapan ini untuk memastikan
        # status reservasi tidak tertinggal.
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

def order_to_dict(
    order: Order,
    user: User | None = None,
) -> dict:
    event = getattr(order, "event", None)

    user_data = None

    if user is not None:
        user_data = {
            "id": user.id,
            "name": user.name or "",
            "email": user.email or "",
        }

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
            event.title
            if event is not None
            else "Event tidak tersedia"
        ),
        "user": user_data,
    }


def payment_to_dict(
    payment: Payment | None,
) -> dict | None:
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
# POST /orders/
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
        # Kunci event untuk mengurangi risiko transaksi
        # bersamaan ketika event sedang diubah.
        event = db.scalar(
            select(Event)
            .where(
                Event.id == data.event_id,
                Event.approval_status == "approved",
                Event.is_deleted.is_(False),
                Event.is_cancelled.is_(False),
            )
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan atau belum tersedia.",
            )

        event_date = as_utc(event.event_date)

        if event_date is not None and event_date <= utc_now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event sudah berlangsung.",
            )

        # Kunci kelas tiket sebelum memeriksa dan mengurangi stok.
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

        if ticket_class.stock < data.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Stok kelas tiket tidak mencukupi. "
                    f"Sisa stok: {ticket_class.stock}."
                ),
            )

        ticket_price = Decimal(str(ticket_class.price))

        if not ticket_price.is_finite() or ticket_price < 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Harga kelas tiket tidak valid.",
            )

        total_price = ticket_price * data.quantity

        now = utc_now()

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            ticket_class_id=ticket_class.id,
            quantity=data.quantity,
            total_price=total_price,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(
                seconds=EXPIRATION_SECONDS
            ),
            stock_reserved=True,
        )

        # Reservasi stok dilakukan dalam transaksi database
        # yang sama dengan pembuatan order.
        ticket_class.stock -= data.quantity

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

        logger.exception(
            "Database error saat membuat pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal disimpan. Silakan coba lagi.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat membuat pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membuat pesanan.",
        )


# ============================================================
# GET MY ORDERS (USER)
# GET /orders/
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
            .options(
                joinedload(Order.event)
            )
            .where(
                Order.user_id == current_user.id
            )
            .order_by(
                Order.created_at.desc()
            )
            .with_for_update(of=Order)
        ).unique().all()

        changed = False

        for order in orders:
            if expire_order_if_needed(db, order):
                changed = True

        if changed:
            db.commit()

        return [
            order_to_dict(
                order,
                user=current_user,
            )
            for order in orders
        ]

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil riwayat pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Riwayat pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat membaca riwayat pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat riwayat pesanan.",
        )


# ============================================================
# GET ORDERS (SUPER ADMIN / ADMIN EVENT)
# GET /orders/admin/all
# ============================================================

@router.get(
    "/admin/all",
    response_model=list[OrderHistoryResponse],
)
def get_all_orders_admin(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_transaction_admin(current_user)

    try:
        query = (
            select(Order)
            .join(Event, Order.event_id == Event.id)
            .options(
                joinedload(Order.event),
                joinedload(Order.user),
            )
        )

        # Admin Event hanya melihat transaksi dari event
        # yang dibuat oleh akun tersebut.
        if current_user.role == "admin_event":
            query = query.where(
                Event.created_by == current_user.id
            )

        orders = db.scalars(
            query
            .order_by(
                Order.created_at.desc()
            )
            .with_for_update(of=Order)
        ).unique().all()

        changed = False

        for order in orders:
            if expire_order_if_needed(db, order):
                changed = True

        if changed:
            db.commit()

        return [
            order_to_dict(
                order,
                user=order.user,
            )
            for order in orders
        ]

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil data pesanan admin."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat membaca data pesanan admin."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat data pesanan.",
        )

# ============================================================
# GET ORDER DETAIL
# GET /orders/{order_id}
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
    try:
        query = (
            select(Order)
            .join(Event, Order.event_id == Event.id)
            .options(
                joinedload(Order.event),
                joinedload(Order.user),
            )
            .where(
                Order.id == order_id
            )
        )

        if current_user.role == "user":
            query = query.where(
                Order.user_id == current_user.id
            )

        elif current_user.role == "admin_event":
            require_approved_event_admin(current_user)

            query = query.where(
                Event.created_by == current_user.id
            )

        elif current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak memiliki akses ke detail pesanan.",
            )

        order = db.scalar(
            query.with_for_update(of=Order)
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Pesanan tidak ditemukan atau "
                    "Anda tidak memiliki akses."
                ),
            )

        # Periksa kedaluwarsa sebelum mengembalikan detail.
        if expire_order_if_needed(db, order):
            db.commit()
            db.refresh(order)

        payment = db.scalar(
            select(Payment)
            .where(
                Payment.order_id == order.id
            )
            .order_by(
                Payment.id.desc()
            )
        )

        result = order_to_dict(
            order,
            user=order.user,
        )

        result["payment"] = payment_to_dict(payment)

        return result

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil detail pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detail pesanan gagal dimuat.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat membaca detail pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memuat detail pesanan.",
        )


# ============================================================
# CANCEL ORDER (USER)
# DELETE /orders/{order_id}
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
                detail=(
                    "Hanya pesanan pending yang "
                    "dapat dibatalkan."
                ),
            )

        # Jika order sudah kedaluwarsa, proses sebagai order
        # kedaluwarsa dan kembalikan stok hanya sekali.
        if is_expired(order):
            expire_order_if_needed(db, order)
            db.commit()

            return {
                "message": "Pesanan sudah kedaluwarsa dan dibatalkan.",
                "order_id": order.id,
                "status": order.status,
            }

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

        if order.stock_reserved:
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

        logger.exception(
            "Database error saat membatalkan pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal dibatalkan.",
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Kesalahan saat membatalkan pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membatalkan pesanan.",
        )
