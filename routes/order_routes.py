
from datetime import datetime
from decimal import Decimal
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, User
from routes.auth_routes import get_current_user


router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)


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


# POST /orders/
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
        # Ambil stok terkini dan kunci baris event.
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
                    f"Sisa stok saat ini: {event.available_tickets}"
                ),
            )

        # Stok diperiksa, tetapi TIDAK dikurangi di tahap ini.
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

    except Exception:
        db.rollback()
        logger.exception("Kesalahan saat membuat pesanan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membuat pesanan",
        )


# GET /orders/
@router.get(
    "/",
    response_model=list[OrderHistoryResponse],
)
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_user(current_user)

    orders = db.scalars(
        select(Order)
        .options(joinedload(Order.event))
        .where(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()

    return [order_to_dict(order) for order in orders]


# GET /orders/admin/all
@router.get(
    "/admin/all",
    response_model=list[OrderHistoryResponse],
)
def get_all_orders_admin(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    orders = db.scalars(
        select(Order)
        .options(joinedload(Order.event))
        .order_by(Order.created_at.desc())
    ).all()

    return [order_to_dict(order) for order in orders]


# GET /orders/{order_id}
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

    return order_to_dict(order)
