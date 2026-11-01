import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, User
from routes.auth_routes import get_current_user


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)

logger = logging.getLogger(__name__)


# =========================================================
# ADMIN ACCESS
# =========================================================

def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya tersedia untuk admin.",
        )

    return current_user


# =========================================================
# RESPONSE SCHEMAS
# =========================================================

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


# =========================================================
# DATABASE HELPER
# =========================================================

def count_rows(
    db: Session,
    model,
    *conditions,
) -> int:
    query = select(
        func.count()
    ).select_from(model)

    if conditions:
        query = query.where(*conditions)

    return int(db.scalar(query) or 0)


# =========================================================
# ADMIN DASHBOARD
# GET /admin/dashboard
# =========================================================

@router.get("/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        # -------------------------------------
        # TOTAL DATA
        # -------------------------------------

        total_events = count_rows(
            db,
            Event,
        )

        total_users = count_rows(
            db,
            User,
            User.role == "user",
        )

        total_orders = count_rows(
            db,
            Order,
        )

        pending_orders = count_rows(
            db,
            Order,
            Order.status == "pending",
        )

        paid_orders = count_rows(
            db,
            Order,
            Order.status == "paid",
        )

        total_payments = count_rows(
            db,
            Payment,
        )

        pending_payments = count_rows(
            db,
            Payment,
            Payment.status == "pending",
        )

        successful_payments = count_rows(
            db,
            Payment,
            Payment.status == "success",
        )

        # -------------------------------------
        # TICKETS SOLD
        # Hanya order berstatus paid
        # -------------------------------------

        tickets_sold = db.scalar(
            select(
                func.coalesce(
                    func.sum(Order.quantity),
                    0,
                )
            ).where(
                Order.status == "paid"
            )
        ) or 0

        # -------------------------------------
        # TOTAL REVENUE
        # Hanya payment berstatus success
        # -------------------------------------

        revenue = db.scalar(
            select(
                func.coalesce(
                    func.sum(Payment.amount),
                    Decimal("0"),
                )
            ).where(
                Payment.status == "success"
            )
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
            "revenue": revenue,
        }

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil dashboard admin."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard admin gagal dimuat.",
        )


# =========================================================
# GET ALL ORDERS
# GET /admin/orders
# =========================================================

@router.get(
    "/orders",
    response_model=list[AdminOrderResponse],
)
def get_all_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        orders = db.scalars(
            select(Order)
            .options(
                joinedload(Order.event),
                joinedload(Order.user),
            )
            .order_by(
                Order.created_at.desc()
            )
        ).all()

        return [
            {
                "id": order.id,
                "user_id": order.user_id,
                "event_id": order.event_id,
                "event_title": (
                    order.event.title
                    if order.event is not None
                    else ""
                ),
                "customer_name": (
                    order.user.name
                    if order.user is not None
                    else ""
                ),
                "customer_email": (
                    order.user.email
                    if order.user is not None
                    else ""
                ),
                "quantity": order.quantity,
                "total_price": order.total_price,
                "status": order.status,
                "created_at": order.created_at,
            }
            for order in orders
        ]

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil semua pesanan."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pesanan gagal dimuat.",
        )


# =========================================================
# GET ALL PAYMENTS
# GET /admin/payments
# =========================================================

@router.get(
    "/payments",
    response_model=list[AdminPaymentResponse],
)
def get_all_payments(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        payments = db.scalars(
            select(Payment)
            .options(
                joinedload(
                    Payment.order
                ).joinedload(
                    Order.user
                ),
                joinedload(
                    Payment.order
                ).joinedload(
                    Order.event
                ),
            )
            .order_by(
                Payment.created_at.desc()
            )
        ).all()

        return [
            {
                "id": payment.id,
                "order_id": payment.order_id,
                "customer_name": (
                    payment.order.user.name
                    if payment.order
                    and payment.order.user
                    else ""
                ),
                "event_title": (
                    payment.order.event.title
                    if payment.order
                    and payment.order.event
                    else ""
                ),
                "amount": payment.amount,
                "payment_method": payment.method,
                "payment_status": payment.status,
                "transaction_id": payment.transaction_id,
                "paid_at": payment.paid_at,
            }
            for payment in payments
        ]

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Database error saat mengambil semua pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pembayaran gagal dimuat.",
        )
