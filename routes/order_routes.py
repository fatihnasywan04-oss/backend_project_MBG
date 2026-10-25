
from datetime import datetime
from decimal import Decimal
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Order, User
from routes.auth_routes import get_current_user


router = APIRouter(prefix="/orders", tags=["Orders"])


# =========================
# REQUEST SCHEMA
# =========================

class OrderCreate(BaseModel):
    event_id: int
    quantity: int = Field(gt=0)


# =========================
# RESPONSE SCHEMA
# =========================

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


class OrderDetailResponse(OrderResponse):
    event_title: str


# =========================
# MEMBUAT PESANAN
# POST /orders/
# =========================

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
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pemesanan hanya tersedia untuk akun user",
        )

    try:
        # Kunci event saat memeriksa dan mengurangi kuota tiket.
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
                detail="Stok tiket tidak mencukupi",
            )

        total_price = Decimal(str(event.price)) * data.quantity

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            quantity=data.quantity,
            total_price=total_price,
            status="pending",
        )

        event.available_tickets -= data.quantity

        db.add(order)
        db.commit()
        db.refresh(order)

        return order

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logging.exception("Gagal membuat pesanan")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal disimpan. Silakan coba lagi.",
        )


# =========================
# RIWAYAT PESANAN USER
# GET /orders/
# =========================

@router.get(
    "/",
    response_model=list[OrderHistoryResponse],
)
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Riwayat pesanan hanya tersedia untuk akun user",
        )

    orders = db.scalars(
        select(Order)
        .where(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()

    return [
        {
            "id": order.id,
            "user_id": order.user_id,
            "event_id": order.event_id,
            "event_title": order.event.title,
            "quantity": order.quantity,
            "total_price": order.total_price,
            "status": order.status,
            "created_at": order.created_at,
        }
        for order in orders
    ]


# =========================
# DETAIL PESANAN USER
# GET /orders/{order_id}
# =========================

@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
)
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Detail pesanan hanya tersedia untuk akun user",
        )

    order = db.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == current_user.id,
        )
    )

    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pesanan tidak ditemukan atau bukan milik akun ini",
        )

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
