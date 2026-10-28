from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Event, User, Order, Payment
from schemas import EventCreate, EventUpdate, EventResponse
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/events", tags=["Events"])


def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )
    return current_user


# =========================================================
# GET SEMUA EVENT
# =========================================================
@router.get("/", response_model=list[EventResponse])
def get_events(db: Session = Depends(get_db)):
    try:
        events = db.scalars(
            select(Event).order_by(Event.event_date.asc())
        ).all()

        return events

    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil daftar event",
        )


# =========================================================
# GET DETAIL EVENT
# =========================================================
@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event tidak ditemukan",
        )

    return event


# =========================================================
# CREATE EVENT — ADMIN
# =========================================================
@router.post(
    "/",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_event(
    data: EventCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    event_data = data.model_dump()

    if event_data.get("image_url") is not None:
        event_data["image_url"] = str(event_data["image_url"])

    # Field ini dikelola backend, bukan dari request.
    event_data.pop("is_cancelled", None)
    event_data.pop("created_by", None)

    # Validasi dasar.
    if event_data.get("price") is not None and event_data["price"] < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Harga tidak boleh negatif",
        )

    if (
        event_data.get("available_tickets") is not None
        and event_data["available_tickets"] < 0
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stok tiket tidak boleh negatif",
        )

    event = Event(
        **event_data,
        created_by=admin.id,
        is_cancelled=False,
    )

    try:
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal disimpan",
        )


# =========================================================
# UPDATE EVENT — ADMIN
# =========================================================
@router.put("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        event = db.scalar(
            select(Event)
            .where(Event.id == event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan",
            )

        if event.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event yang dibatalkan tidak dapat diedit",
            )

        update_data = data.model_dump(exclude_unset=True)

        # Field yang tidak boleh diubah melalui payload edit.
        update_data.pop("is_cancelled", None)
        update_data.pop("created_by", None)
        update_data.pop("id", None)

        required_fields = {
            "title",
            "description",
            "location",
            "event_date",
            "price",
            "available_tickets",
        }

        for key, value in update_data.items():
            if key in required_fields and value is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{key} tidak boleh kosong",
                )

            if key in {"title", "description", "location"}:
                if isinstance(value, str):
                    value = value.strip()

                    if not value:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{key} tidak boleh kosong",
                        )

            if key == "price" and value < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Harga tidak boleh negatif",
                )

            if key == "available_tickets":
                if value < 0:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Stok tidak boleh negatif",
                    )

                # Jumlah tiket yang sudah dibayar tidak boleh melebihi stok baru.
                sold_quantity = db.scalar(
                    select(
                        __import__("sqlalchemy").func.coalesce(
                            __import__("sqlalchemy").func.sum(Order.quantity),
                            0,
                        )
                    ).where(
                        Order.event_id == event_id,
                        Order.status == "paid",
                    )
                ) or 0

                if value < sold_quantity:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Stok event tidak boleh di bawah jumlah tiket "
                            f"terjual ({sold_quantity})."
                        ),
                    )

            if key == "image_url" and value is not None:
                value = str(value)

            setattr(event, key, value)

        db.commit()
        db.refresh(event)

        return event

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal diperbarui",
        )


# =========================================================
# CANCEL EVENT — ADMIN
# =========================================================
@router.post("/{event_id}/cancel")
def cancel_event(
    event_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        event = db.scalar(
            select(Event)
            .where(Event.id == event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan",
            )

        if event.is_cancelled:
            return {
                "message": "Event sudah dibatalkan",
                "event_id": event.id,
                "is_cancelled": True,
            }

        # Periksa apakah ada pesanan yang sudah dibayar.
        paid_order = db.scalar(
            select(Order.id)
            .where(
                Order.event_id == event_id,
                Order.status == "paid",
            )
            .limit(1)
        )

        # Batalkan pesanan pending dan pembayaran pending.
        pending_orders = db.scalars(
            select(Order)
            .where(
                Order.event_id == event_id,
                Order.status == "pending",
            )
            .with_for_update()
        ).all()

        for order in pending_orders:
            # Kembalikan stok hanya jika sebelumnya memang sudah direservasi.
            if order.stock_reserved:
                ticket_class = db.scalar(
                    select(__import__("models").TicketClass)
                    .where(
                        __import__("models").TicketClass.id
                        == order.ticket_class_id
                    )
                    .with_for_update()
                )

                if ticket_class is not None:
                    ticket_class.stock += order.quantity

                order.stock_reserved = False

            order.status = "cancelled"

            pending_payments = db.scalars(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.status == "pending",
                )
                .with_for_update()
            ).all()

            for payment in pending_payments:
                payment.status = "failed"

        event.is_cancelled = True

        db.commit()

        return {
            "message": (
                "Event berhasil dibatalkan. "
                "Riwayat pesanan tetap disimpan."
            ),
            "event_id": event.id,
            "is_cancelled": True,
            "has_paid_orders": paid_order is not None,
            "refund_required": paid_order is not None,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal dibatalkan",
        )


# =========================================================
# DELETE EVENT — ADMIN
# =========================================================
@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        event = db.scalar(
            select(Event)
            .where(Event.id == event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan",
            )

        # Jangan menghapus event yang sudah memiliki riwayat pesanan.
        existing_order = db.scalar(
            select(Order.id)
            .where(Order.event_id == event_id)
            .limit(1)
        )

        if existing_order is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Event tidak dapat dihapus permanen karena sudah memiliki "
                    "pesanan. Gunakan endpoint pembatalan event agar riwayat "
                    "pesanan dan pembayaran tetap aman."
                ),
            )

        db.delete(event)
        db.commit()

        return {
            "message": "Event berhasil dihapus",
            "event_id": event_id,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal dihapus",
        )
