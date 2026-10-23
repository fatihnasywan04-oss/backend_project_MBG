from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Order, User
from routes.auth_routes import get_current_user


router = APIRouter(prefix="/orders", tags=["Orders"])


class OrderCreate(BaseModel):
    event_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=10)


class OrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    quantity: int
    total_price: Decimal
    status: str

    class Config:
        from_attributes = True


# Membeli tiket
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
            status_code=403,
            detail="Pembelian tiket hanya tersedia untuk akun user",
        )

    # Mengunci baris event selama transaksi agar stok tidak
    # terbeli melebihi jumlah yang tersedia.
    event = db.scalar(
        select(Event)
        .where(Event.id == data.event_id)
        .with_for_update()
    )

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event tidak ditemukan",
        )

    if data.quantity > event.available_tickets:
        raise HTTPException(
            status_code=400,
            detail="Stok tiket tidak mencukupi",
        )

    total_price = event.price * data.quantity

    order = Order(
        user_id=current_user.id,
        event_id=event.id,
        quantity=data.quantity,
        total_price=total_price,
        status="pending",
    )

    event.available_tickets -= data.quantity

    try:
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Pesanan gagal disimpan",
        )


# Melihat riwayat pembelian pengguna yang login
@router.get("/", response_model=list[OrderResponse])
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "user":
        raise HTTPException(
            status_code=403,
            detail="Riwayat pembelian hanya tersedia untuk akun user",
        )

    orders = db.scalars(
        select(Order)
        .where(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()

    return orders