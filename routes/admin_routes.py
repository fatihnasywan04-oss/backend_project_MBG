
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Order, Payment, User
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])


def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya tersedia untuk admin",
        )
    return current_user


class AdminOrderResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    event_title: str
    customer_name: str
    customer_email: str
    quantity: int
    total_price: Decimal
    status: str
    created_at: object

    model_config = ConfigDict(from_attributes=True)


class AdminPaymentResponse(BaseModel):
    id: int
    order_id: int
    customer_name: str
    event_title: str
    amount: Decimal
    payment_method: str | None
    payment_status: str
    transaction_id: str | None
    paid_at: object | None

    model_config = ConfigDict(from_attributes=True)


@router.get("/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    total_events = db.scalar(
        select(func.count(Event.id))
    ) or 0

    total_orders = db.scalar(
        select(func.count(Order.id))
    ) or 0

    total_payments = db.scalar(
        select(func.count(Payment.id))
    ) or 0

    paid_orders = db.scalar(
        select(func.count(Order.id)).where(
            Order.status == "paid"
        )
    ) or 0

    tickets_sold = db.scalar(
        select(func.coalesce(func.sum(Order.quantity), 0))
        .where(Order.status == "paid")
    ) or 0

    revenue = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.payment_status == "success")
    ) or 0

    pending_payments = db.scalar(
        select(func.count(Payment.id)).where(
            Payment.payment_status == "pending"
        )
    ) or 0

    return {
        "total_events": total_events,
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "total_payments": total_payments,
        "tickets_sold": int(tickets_sold),
        "revenue": float(revenue),
        "pending_payments": pending_payments,
    }


@router.get("/orders", response_model=list[AdminOrderResponse])
def get_all_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    orders = db.scalars(
        select(Order).order_by(Order.created_at.desc())
    ).all()

    return [
        {
            "id": order.id,
            "user_id": order.user_id,
            "event_id": order.event_id,
            "event_title": order.event.title,
            "customer_name": order.user.name,
            "customer_email": order.user.email,
            "quantity": order.quantity,
            "total_price": order.total_price,
            "status": order.status,
            "created_at": order.created_at,
        }
        for order in orders
    ]


@router.get("/payments", response_model=list[AdminPaymentResponse])
def get_all_payments(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    payments = db.scalars(
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .order_by(Payment.created_at.desc())
    ).all()

    return [
        {
            "id": payment.id,
            "order_id": payment.order_id,
            "customer_name": payment.order.user.name,
            "event_title": payment.order.event.title,
            "amount": payment.amount,
            "payment_method": payment.payment_method,
            "payment_status": payment.payment_status,
            "transaction_id": payment.transaction_id,
            "paid_at": payment.paid_at,
        }
        for payment in payments
    ]
