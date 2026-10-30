import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    status,
)
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Event, User, Order, Payment, TicketClass
from schemas import EventCreate, EventUpdate, EventResponse
from routes.auth_routes import get_current_user


router = APIRouter(prefix="/events", tags=["Events"])


# =========================================================
# KONFIGURASI UPLOAD GAMBAR
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

EVENT_UPLOAD_DIR = BASE_DIR / "uploads" / "events"
EVENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


# =========================================================
# ADMIN AUTHORIZATION
# =========================================================

def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )

    return current_user


# =========================================================
# VALIDASI INPUT EVENT
# =========================================================

def validate_event_data(data: dict):
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()

        if not data["title"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Judul event tidak boleh kosong",
            )

    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()

    if "location" in data and data["location"] is not None:
        data["location"] = data["location"].strip()

        if not data["location"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Lokasi event tidak boleh kosong",
            )

    if "price" in data and data["price"] is not None:
        if data["price"] < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Harga tidak boleh negatif",
            )

    if "available_tickets" in data and data["available_tickets"] is not None:
        if data["available_tickets"] < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Stok tiket tidak boleh negatif",
            )

    if "image_url" in data and data["image_url"] is not None:
        data["image_url"] = str(data["image_url"])

    return data


# =========================================================
# UPLOAD POSTER EVENT — ADMIN
# POST /events/upload-image
# =========================================================

@router.post("/upload-image")
async def upload_event_image(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
):
    # Validasi tipe file berdasarkan Content-Type.
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        await file.close()

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Format poster harus JPG, PNG, atau WEBP.",
        )

    extension = ALLOWED_IMAGE_TYPES[file.content_type]

    # Nama file acak untuk mencegah benturan nama.
    filename = f"{uuid.uuid4().hex}{extension}"
    destination = EVENT_UPLOAD_DIR / filename

    size = 0

    try:
        # Baca per bagian agar tidak memuat seluruh file sekaligus
        # ke memori.
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_IMAGE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Ukuran poster maksimal 5 MB.",
                    )

                output.write(chunk)

        if size == 0:
            destination.unlink(missing_ok=True)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File poster kosong.",
            )

        # URL relatif. Flutter perlu menggabungkannya dengan base URL.
        return {
            "message": "Poster berhasil diunggah.",
            "image_url": f"/uploads/events/{filename}",
        }

    except HTTPException:
        destination.unlink(missing_ok=True)
        raise

    except OSError:
        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal menyimpan poster.",
        )

    finally:
        await file.close()


# =========================================================
# GET SEMUA EVENT
# GET /events/
# =========================================================

@router.get("/", response_model=list[EventResponse])
def get_events(db: Session = Depends(get_db)):
    try:
        events = db.scalars(
            select(Event).order_by(Event.event_date.asc())
        ).all()

        return events

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil daftar event",
        )


# =========================================================
# GET DETAIL EVENT
# GET /events/{event_id}
# =========================================================

@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    try:
        event = db.get(Event, event_id)

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan",
            )

        return event

    except HTTPException:
        raise

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil detail event",
        )


# =========================================================
# CREATE EVENT — ADMIN
# POST /events/
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

    # Field ini dikelola backend.
    event_data.pop("id", None)
    event_data.pop("created_by", None)
    event_data.pop("is_cancelled", None)
    event_data.pop("created_at", None)

    event_data = validate_event_data(event_data)

    try:
        event = Event(
            **event_data,
            created_by=admin.id,
            is_cancelled=False,
        )

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
# PUT /events/{event_id}
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

        # Field yang tidak boleh diedit melalui request.
        update_data.pop("id", None)
        update_data.pop("created_by", None)
        update_data.pop("created_at", None)
        update_data.pop("is_cancelled", None)

        update_data = validate_event_data(update_data)

        if "available_tickets" in update_data:
            new_stock = update_data["available_tickets"]

            if new_stock is not None:
                # Jumlah tiket yang sudah dibayar.
                sold_quantity = db.scalar(
                    select(
                        func.coalesce(func.sum(Order.quantity), 0)
                    ).where(
                        Order.event_id == event_id,
                        Order.status == "paid",
                    )
                ) or 0

                # Pesanan pending yang masih mereservasi stok.
                reserved_quantity = db.scalar(
                    select(
                        func.coalesce(func.sum(Order.quantity), 0)
                    ).where(
                        Order.event_id == event_id,
                        Order.status == "pending",
                        Order.stock_reserved.is_(True),
                    )
                ) or 0

                minimum_stock = sold_quantity + reserved_quantity

                if new_stock < minimum_stock:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Stok tidak boleh kurang dari jumlah tiket "
                            "terjual dan yang sedang direservasi "
                            f"({minimum_stock} tiket)."
                        ),
                    )

        for key, value in update_data.items():
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
# POST /events/{event_id}/cancel
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

        paid_order = db.scalar(
            select(Order.id)
            .where(
                Order.event_id == event_id,
                Order.status == "paid",
            )
            .limit(1)
        )

        pending_orders = db.scalars(
            select(Order)
            .where(
                Order.event_id == event_id,
                Order.status == "pending",
            )
            .with_for_update()
        ).all()

        for order in pending_orders:
            # Kembalikan stok jika sebelumnya direservasi.
            if order.stock_reserved:
                ticket_class = db.scalar(
                    select(TicketClass)
                    .where(TicketClass.id == order.ticket_class_id)
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
# DELETE /events/{event_id}
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

        # Jangan menghapus event yang memiliki riwayat pesanan.
        existing_order = db.scalar(
            select(Order.id)
            .where(Order.event_id == event_id)
            .limit(1)
        )

        if existing_order is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Event tidak dapat dihapus permanen karena sudah "
                    "memiliki pesanan. Gunakan endpoint pembatalan event "
                    "agar riwayat pesanan dan pembayaran tetap aman."
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