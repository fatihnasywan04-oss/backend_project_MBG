
from datetime import datetime, timezone

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
        raise HTTPException(status_code=403, detail="Akses hanya untuk admin")
    return current_user


@router.get("/", response_model=list[EventResponse])
def get_events(db: Session = Depends(get_db)):
    return db.scalars(
        select(Event).order_by(Event.event_date.asc())
    ).all()


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    return event


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
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
        raise HTTPException(status_code=500, detail="Event gagal disimpan")


@router.put("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
    )

    if event is None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    if event.is_cancelled:
        raise HTTPException(status_code=400, detail="Event yang dibatalkan tidak dapat diedit")

    update_data = data.model_dump(exclude_unset=True)

    # Admin tidak boleh mengaktifkan kembali event melalui payload edit.
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
            raise HTTPException(status_code=422, detail=f"{key} tidak boleh kosong")

        if key in {"title", "description", "location"}:
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    raise HTTPException(status_code=422, detail=f"{key} tidak boleh kosong")

        if key == "price" and value < 0:
            raise HTTPException(status_code=422, detail="Harga tidak boleh negatif")

        if key == "available_tickets":
            if value < 0:
                raise HTTPException(status_code=422, detail="Stok tidak boleh negatif")

            # Jangan izinkan stok menjadi lebih kecil daripada tiket yang sudah terjual.
            sold_quantity = sum(
                order.quantity
                for order in db.scalars(
                    select(Order).where(
                        Order.event_id == event_id,
                        Order.status == "paid",
                    )
                ).all()
            )

            if value < sold_quantity:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Stok event tidak boleh di bawah jumlah tiket terjual "
                        f"({sold_quantity})."
                    ),
                )

        if key == "image_url" and value is not None:
            value = str(value)

        setattr(event, key, value)

    try:
        db.commit()
        db.refresh(event)
        return event

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Event gagal diperbarui")


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
            raise HTTPException(status_code=404, detail="Event tidak ditemukan")

        if event.is_cancelled:
            return {
                "message": "Event sudah dibatalkan",
                "event_id": event.id,
                "is_cancelled": True,
            }

        # Tolak jika masih ada pembayaran sukses untuk order paid.
        paid_order = db.scalar(
            select(Order.id)
            .where(
                Order.event_id == event_id,
                Order.status == "paid",
            )
            .limit(1)
        )

        # Event tetap boleh dibatalkan meskipun sudah dibayar, tetapi
        # respons memperingatkan admin bahwa transaksi/refund perlu ditangani.
        pending_orders = db.scalars(
            select(Order)
            .where(
                Order.event_id == event_id,
                Order.status == "pending",
            )
            .with_for_update()
        ).all()

        for order in pending_orders:
            order.status = "cancelled"

            pending_payments = db.scalars(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.payment_status == "pending",
                )
                .with_for_update()
            ).all()

            for payment in pending_payments:
                payment.payment_status = "failed"

        event.is_cancelled = True
        db.commit()

        return {
            "message": "Event berhasil dibatalkan. Riwayat pesanan tetap disimpan.",
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
        raise HTTPException(status_code=500, detail="Event gagal dibatalkan")


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
            raise HTTPException(status_code=404, detail="Event tidak ditemukan")

        existing_order = db.scalar(
            select(Order.id)
            .where(Order.event_id == event_id)
            .limit(1)
        )

        if existing_order is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Event tidak dapat dihapus permanen karena sudah memiliki "
                    "pesanan. Gunakan endpoint pembatalan event agar riwayat "
                    "pesanan dan pembayaran tetap aman."
                ),
            )

        db.delete(event)
        db.commit()

        return {"message": "Event berhasil dihapus", "event_id": event_id}

    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Event gagal dihapus")
