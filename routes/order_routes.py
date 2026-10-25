
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, User
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)

EXPIRATION_MINUTES = 15


class OrderCreate(BaseModel):
    event_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class OrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    quantity: int
    total_price: Decimal
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderHistoryResponse(OrderResponse):
    event_title: str


class OrderDetailResponse(OrderHistoryResponse):
    pass


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


def is_expired(order: Order) -> bool:
    created_at = order.created_at

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) >= (
        created_at + timedelta(minutes=EXPIRATION_MINUTES)
    )


def expire_order_if_needed(db: Session, order: Order) -> bool:
    """Batalkan pesanan pending yang sudah lebih dari 15 menit."""
    if order.status != "pending" or not is_expired(order):
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


def order_to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "event_id": order.event_id,
        "event_title": order.event.title,
        "quantity": order.quantity,
        "total_price": order.total_price,
        "status": order.status,
        "created_at": order.created_at,
    }


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
                detail="Event tidak ditemukan",
            )

        if event.available_tickets < data.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Stok tiket tidak mencukupi. "
                    f"Sisa stok: {event.available_tickets}"
                ),
            )

        # Stok belum dikurangi pada tahap pembuatan pesanan.
        total_price = Decimal(str(event.price)) * data.quantity

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            quantity=data.quantity,
            total_price=total_price,
            status="pending",
        )

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

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil riwayat pesanan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Riwayat pesanan gagal dimuat",
        )


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
        ).unique().all()

        changed = False

        for order in orders:
            if expire_order_if_needed(db, order):
                changed = True

        if changed:
            db.commit()

        return [order_to_dict(order) for order in orders]

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Gagal mengambil seluruh pesanan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data pesanan gagal dimuat",
        )


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
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pesanan tidak ditemukan atau bukan milik akun ini",
            )

        if expire_order_if_needed(db, order):
            db.commit()
            db.refresh(order)

        return order_to_dict(order)

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

        # Jika sudah lewat batas waktu, simpan sebagai cancelled.
        if expire_order_if_needed(db, order):
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pesanan sudah kedaluwarsa dan dibatalkan",
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
