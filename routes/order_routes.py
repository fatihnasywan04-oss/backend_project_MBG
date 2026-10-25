
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
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
    quantity: int


# =========================
# RESPONSE SCHEMA
# =========================

class OrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    quantity: int
    total_price: float
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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

    if data.quantity <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Jumlah tiket harus lebih dari 0",
        )

    try:
        # Kunci data event selama transaksi jika database mendukungnya.
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

        if event.stock < data.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Stok tiket tidak mencukupi",
            )

        order = Order(
            user_id=current_user.id,
            event_id=event.id,
            quantity=data.quantity,
            total_price=event.price * data.quantity,
            status="pending",
        )

        # Kurangi stok sesuai jumlah tiket yang dipesan.
        event.stock -= data.quantity

        db.add(order)
        db.commit()
        db.refresh(order)

        return order

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pesanan gagal disimpan",
        )


# =========================
# DAFTAR PESANAN MILIK USER
# GET /orders/
# =========================

@router.get(
    "/",
    response_model=list[OrderResponse],
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

    return orders


# =========================
# DETAIL SATU PESANAN
# GET /orders/{order_id}
# =========================

@router.get(
    "/{order_id}",
    response_model=OrderResponse,
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

    # Pastikan pesanan memang milik user yang sedang login.
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

    return order
