
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, User
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])


def require_admin(
    current_user: User = Depends(get_current_user),
):
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
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminPaymentResponse(BaseModel):
    id: int
    order_id: int
    customer_name: str
    event_title: str
    amount: Decimal
    payment_method: str | None = None
    payment_status: str
    transaction_id: str | None = None
    paid_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


@router.get("/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    def count_rows(model, *conditions):
        query = select(func.count()).select_from(model)

        if conditions:
            query = query.where(*conditions)

        return db.scalar(query) or 0

    total_events = count_rows(Event)
    total_users = count_rows(User, User.role == "user")
    total_orders = count_rows(Order)
    pending_orders = count_rows(
        Order, Order.status == "pending"
    )
    paid_orders = count_rows(
        Order, Order.status == "paid"
    )
    total_payments = count_rows(Payment)
    pending_payments = count_rows(
        Payment, Payment.payment_status == "pending"
    )
    successful_payments = count_rows(
        Payment, Payment.payment_status == "success"
    )

    tickets_sold = db.scalar(
        select(func.coalesce(func.sum(Order.quantity), 0))
        .where(Order.status == "paid")
    ) or 0

    revenue = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.payment_status == "success")
    ) or Decimal("0")

    return {
        "total_events": total_events,
        "total_users": total_users,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "paid_orders": paid_orders,
        "total_payments": total_payments,
        "pending_payments": pending_payments,
        "successful_payments": successful_payments,
        "tickets_sold": int(tickets_sold),
        "revenue": float(revenue),
    }


@router.get(
    "/orders",
    response_model=list[AdminOrderResponse],
)
def get_all_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    orders = db.scalars(
        select(Order)
        .options(
            joinedload(Order.event),
            joinedload(Order.user),
        )
        .order_by(Order.created_at.desc())
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


@router.get(
    "/payments",
    response_model=list[AdminPaymentResponse],
)
def get_all_payments(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    payments = db.scalars(
        select(Payment)
        .options(
            joinedload(Payment.order).joinedload(Order.user),
            joinedload(Payment.order).joinedload(Order.event),
        )
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
