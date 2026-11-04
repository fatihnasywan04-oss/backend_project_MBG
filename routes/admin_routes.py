import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Event, Order, Payment, User
from schemas import AdminEventApplicationReview
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
            detail="Akses hanya tersedia untuk Super Admin.",
        )

    return current_user


def require_admin_or_event(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role == "admin":
        return current_user

    if current_user.role == "admin_event":
        if current_user.admin_event_status != "approved":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akun Admin Event belum disetujui.",
            )

        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Akses hanya tersedia untuk admin.",
    )


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


class AdminEventApplicationResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    admin_event_status: str
    admin_event_reviewed_by: int | None = None
    admin_event_reviewed_at: datetime | None = None
    admin_event_rejection_reason: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# DATABASE HELPERS
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


def get_owned_event_ids_query(
    current_user: User,
):
    query = select(Event.id)

    if current_user.role == "admin_event":
        query = query.where(
            Event.created_by == current_user.id
        )

    return query


def get_order_scope_conditions(
    current_user: User,
):
    if current_user.role == "admin":
        return []

    return [
        Order.event_id.in_(
            get_owned_event_ids_query(current_user)
        )
    ]


def get_payment_scope_conditions(
    current_user: User,
):
    if current_user.role == "admin":
        return []

    return [
        Payment.order_id.in_(
            select(Order.id).where(
                Order.event_id.in_(
                    get_owned_event_ids_query(current_user)
                )
            )
        )
    ]


# =========================================================
# ADMIN EVENT APPLICATIONS
# =========================================================

@router.get(
    "/admin-event-applications",
    response_model=list[AdminEventApplicationResponse],
)
def get_admin_event_applications(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Melihat seluruh pengajuan akun Admin Event.
    Hanya Super Admin.
    """

    try:
        users = db.scalars(
            select(User)
            .where(
                User.admin_event_status.in_(
                    ["pending", "approved", "rejected"]
                )
            )
            .order_by(User.created_at.desc())
        ).all()

        return users

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil pengajuan Admin Event."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pengajuan Admin Event gagal dimuat.",
        )


@router.post(
    "/admin-event-applications/{user_id}/review",
    response_model=AdminEventApplicationResponse,
)
def review_admin_event_application(
    user_id: int,
    data: AdminEventApplicationReview,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    """
    Menyetujui atau menolak pengajuan Admin Event.
    Hanya Super Admin.
    """

    try:
        applicant = db.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )

        if applicant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User tidak ditemukan.",
            )

        if applicant.admin_event_status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Pengajuan user ini tidak sedang "
                    "menunggu persetujuan."
                ),
            )

        if applicant.role != "user":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Hanya user biasa yang dapat "
                    "disetujui menjadi Admin Event."
                ),
            )

        applicant.admin_event_status = data.status
        applicant.admin_event_reviewed_by = current_admin.id
        applicant.admin_event_reviewed_at = datetime.now(
            timezone.utc
        )

        if data.status == "approved":
            applicant.role = "admin_event"
            applicant.admin_event_rejection_reason = None

        else:
            applicant.role = "user"
            applicant.admin_event_rejection_reason = (
                data.rejection_reason.strip()
            )

        db.commit()
        db.refresh(applicant)

        return applicant

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal memproses pengajuan Admin Event."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pengajuan Admin Event gagal diproses.",
        )


# =========================================================
# ADMIN DASHBOARD
# GET /admin/dashboard
# =========================================================

@router.get("/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_event),
):
    try:
        is_super_admin = current_user.role == "admin"

        event_conditions = []

        if not is_super_admin:
            event_conditions.append(
                Event.created_by == current_user.id
            )

        event_ids_query = get_owned_event_ids_query(
            current_user
        )

        total_events = count_rows(
            db,
            Event,
            *event_conditions,
            Event.is_deleted.is_(False),
        )

        total_users = (
            count_rows(
                db,
                User,
                User.role == "user",
            )
            if is_super_admin
            else 0
        )

        order_conditions = get_order_scope_conditions(
            current_user
        )

        payment_conditions = get_payment_scope_conditions(
            current_user
        )

        total_orders = count_rows(
            db,
            Order,
            *order_conditions,
        )

        pending_orders = count_rows(
            db,
            Order,
            *order_conditions,
            Order.status == "pending",
        )

        paid_orders = count_rows(
            db,
            Order,
            *order_conditions,
            Order.status == "paid",
        )

        total_payments = count_rows(
            db,
            Payment,
            *payment_conditions,
        )

        pending_payments = count_rows(
            db,
            Payment,
            *payment_conditions,
            Payment.status == "pending",
        )

        successful_payments = count_rows(
            db,
            Payment,
            *payment_conditions,
            Payment.status == "success",
        )

        tickets_sold = db.scalar(
            select(
                func.coalesce(
                    func.sum(Order.quantity),
                    0,
                )
            ).where(
                *order_conditions,
                Order.status == "paid",
            )
        ) or 0

        revenue = db.scalar(
            select(
                func.coalesce(
                    func.sum(Payment.amount),
                    Decimal("0"),
                )
            )
            .join(
                Order,
                Payment.order_id == Order.id,
            )
            .where(
                *order_conditions,
                Payment.status == "success",
            )
        ) or Decimal("0")

        response = {
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

        if is_super_admin:
            response["pending_admin_event_applications"] = (
                count_rows(
                    db,
                    User,
                    User.admin_event_status == "pending",
                )
            )

        return response

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
    current_user: User = Depends(require_admin_or_event),
):
    try:
        query = (
            select(Order)
            .join(Event, Order.event_id == Event.id)
            .options(
                joinedload(Order.event),
                joinedload(Order.user),
            )
        )

        if current_user.role == "admin_event":
            query = query.where(
                Event.created_by == current_user.id
            )

        orders = db.scalars(
            query.order_by(
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
            "Database error saat mengambil pesanan."
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
    current_user: User = Depends(require_admin_or_event),
):
    try:
        query = (
            select(Payment)
            .join(
                Order,
                Payment.order_id == Order.id,
            )
            .join(
                Event,
                Order.event_id == Event.id,
            )
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
        )

        if current_user.role == "admin_event":
            query = query.where(
                Event.created_by == current_user.id
            )

        payments = db.scalars(
            query.order_by(
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
            "Database error saat mengambil pembayaran."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pembayaran gagal dimuat.",
        )