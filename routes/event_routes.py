
import logging
import uuid
import re
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

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
from models import (
    Event,
    User,
    Order,
    Payment,
    TicketClass,
    EventUpdateRequest,
    EventDeletionRequest,
)
from schemas import (
    EventCreate,
    EventUpdate,
    EventResponse,
    EventUpdateRequestResponse,
    EventUpdateRequestReview,
    EventDeletionRequestCreate,
    EventDeletionRequestResponse,
    EventDeletionRequestReview,
)
from routes.auth_routes import get_current_user


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/events",
    tags=["Events"],
)


# =========================================================
# KONFIGURASI UPLOAD GAMBAR
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

EVENT_UPLOAD_DIR = BASE_DIR / "uploads" / "events"
EVENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_SIZE = 5 * 1024 * 1024

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


# =========================================================
# KONSTANTA
# =========================================================

ALLOWED_EVENT_FIELDS = {
    "title",
    "description",
    "location",
    "category",
    "instagram_username",
    "event_date",
    "end_date",
    "price",
    "available_tickets",
    "image_url",
}

ALLOWED_TICKET_FIELDS = {
    "name",
    "price",
    "available_tickets",
}


# =========================================================
# AUTHORIZATION
# =========================================================

def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk Super Admin.",
        )

    return current_user


def require_event_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in ("admin", "admin_event"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin.",
        )

    if (
        current_user.role == "admin_event"
        and current_user.admin_event_status != "approved"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun Admin Event belum disetujui.",
        )

    return current_user


def ensure_event_owner(
    event: Event,
    current_user: User,
):
    if (
        current_user.role == "admin_event"
        and event.created_by != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Anda hanya dapat mengelola event milik sendiri.",
        )


# =========================================================
# VALIDASI INPUT EVENT
# =========================================================

def validate_event_data(data: dict):
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()

        if not data["title"]:
            raise HTTPException(
                status_code=422,
                detail="Judul event tidak boleh kosong.",
            )

    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()

        if not data["description"]:
            raise HTTPException(
                status_code=422,
                detail="Deskripsi event tidak boleh kosong.",
            )

    if "location" in data and data["location"] is not None:
        data["location"] = data["location"].strip()

        if not data["location"]:
            raise HTTPException(
                status_code=422,
                detail="Lokasi event tidak boleh kosong.",
            )

    if (
        "instagram_username" in data
        and data["instagram_username"] is not None
    ):
        username = str(data["instagram_username"]).strip()
        username = username.removeprefix("@").strip()

        if not username:
            data["instagram_username"] = None

        elif (
            len(username) > 30
            or not re.fullmatch(r"[A-Za-z0-9._]+", username)
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Username Instagram tidak valid. "
                    "Gunakan maksimal 30 karakter berupa huruf, "
                    "angka, titik, atau garis bawah."
                ),
            )

        else:
            data["instagram_username"] = username

    if "price" in data and data["price"] is not None:
        try:
            price = Decimal(str(data["price"]))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Harga event tidak valid.",
            )

        if not price.is_finite() or price < 0:
            raise HTTPException(
                status_code=422,
                detail="Harga tidak boleh negatif atau tidak valid.",
            )

        data["price"] = price

    if (
        "available_tickets" in data
        and data["available_tickets"] is not None
    ):
        try:
            raw_stock = data["available_tickets"]

            if isinstance(raw_stock, bool):
                raise ValueError

            stock = int(raw_stock)

            if str(raw_stock).strip() != str(stock):
                raise ValueError

        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Stok tiket harus berupa bilangan bulat.",
            )

        if stock < 0:
            raise HTTPException(
                status_code=422,
                detail="Stok tiket tidak boleh negatif.",
            )

        data["available_tickets"] = stock

    if "image_url" in data and data["image_url"] is not None:
        data["image_url"] = str(data["image_url"])

    return data


def parse_datetime_value(value):
    """
    JSON pada EventUpdateRequest dapat menyimpan datetime
    sebagai string ISO. Konversikan kembali sebelum diterapkan
    ke model Event.
    """
    if value is None or isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Format tanggal event tidak valid.",
            )

    raise HTTPException(
        status_code=422,
        detail="Format tanggal event tidak valid.",
    )


def normalize_event_dates(data: dict):
    for field in ("event_date", "end_date"):
        if field in data:
            data[field] = parse_datetime_value(data[field])

    return data


def validate_event_dates(
    update_data: dict,
    event: Event,
):
    new_start = update_data.get(
        "event_date",
        event.event_date,
    )

    new_end = update_data.get(
        "end_date",
        event.end_date,
    )

    if new_start is not None and new_end is not None:
        if (
            (new_start.tzinfo is None)
            != (new_end.tzinfo is None)
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Format timezone tanggal mulai dan selesai "
                    "harus konsisten."
                ),
            )

        if new_end < new_start:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Tanggal selesai tidak boleh lebih awal "
                    "dari tanggal mulai event."
                ),
            )


# =========================================================
# CREATE EVENT
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
    current_user: User = Depends(require_event_admin),
):
    try:
        event_data = data.model_dump()

        event_data = validate_event_data(event_data)
        event_data = normalize_event_dates(event_data)

        # Validasi konsistensi timezone tanggal.
        start_date = event_data["event_date"]
        end_date = event_data.get("end_date")

        if end_date is not None:
            if (
                (start_date.tzinfo is None)
                != (end_date.tzinfo is None)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Format timezone tanggal mulai dan "
                        "selesai harus konsisten."
                    ),
                )

            if end_date < start_date:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Tanggal selesai tidak boleh lebih awal "
                        "dari tanggal mulai event."
                    ),
                )

        # Status event berdasarkan role pengguna.
        if current_user.role == "admin":
            approval_status = "approved"
            approval_reviewed_by = current_user.id
            approval_reviewed_at = datetime.now(timezone.utc)

        elif current_user.role == "admin_event":
            approval_status = "pending"
            approval_reviewed_by = None
            approval_reviewed_at = None

        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role tidak memiliki akses membuat event.",
            )

        event = Event(
            title=event_data["title"],
            description=event_data["description"],
            location=event_data["location"],
            category=event_data.get("category"),
            instagram_username=event_data.get(
                "instagram_username"
            ),
            event_date=start_date,
            end_date=end_date,
            price=event_data["price"],
            available_tickets=event_data["available_tickets"],
            image_url=event_data.get("image_url"),
            created_by=current_user.id,
            approval_status=approval_status,
            approval_reviewed_by=approval_reviewed_by,
            approval_reviewed_at=approval_reviewed_at,
            is_cancelled=False,
            is_deleted=False,
        )

        db.add(event)
        db.commit()
        db.refresh(event)

        logger.info(
            "Event berhasil dibuat: event_id=%s, user_id=%s, "
            "role=%s, approval_status=%s",
            event.id,
            current_user.id,
            current_user.role,
            event.approval_status,
        )

        return event

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal membuat event: user_id=%s",
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal menyimpan event ke database.",
        )


# =========================================================
# VALIDASI STOK EVENT
# =========================================================

def get_minimum_event_stock(
    db: Session,
    event_id: int,
) -> int:
    sold_quantity = db.scalar(
        select(
            func.coalesce(
                func.sum(Order.quantity),
                0,
            )
        ).where(
            Order.event_id == event_id,
            Order.status == "paid",
        )
    ) or 0

    reserved_quantity = db.scalar(
        select(
            func.coalesce(
                func.sum(Order.quantity),
                0,
            )
        ).where(
            Order.event_id == event_id,
            Order.status == "pending",
            Order.stock_reserved.is_(True),
        )
    ) or 0

    return int(sold_quantity + reserved_quantity)


def validate_event_stock(
    db: Session,
    event_id: int,
    new_stock: int,
):
    minimum_stock = get_minimum_event_stock(
        db,
        event_id,
    )

    if new_stock < minimum_stock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Stok tidak boleh kurang dari jumlah tiket "
                "terjual dan yang sedang direservasi "
                f"({minimum_stock} tiket)."
            ),
        )


def get_minimum_ticket_class_stock(
    db: Session,
    ticket_class_id: int,
) -> int:
    sold_quantity = db.scalar(
        select(
            func.coalesce(
                func.sum(Order.quantity),
                0,
            )
        ).where(
            Order.ticket_class_id == ticket_class_id,
            Order.status == "paid",
        )
    ) or 0

    reserved_quantity = db.scalar(
        select(
            func.coalesce(
                func.sum(Order.quantity),
                0,
            )
        ).where(
            Order.ticket_class_id == ticket_class_id,
            Order.status == "pending",
            Order.stock_reserved.is_(True),
        )
    ) or 0

    return int(sold_quantity + reserved_quantity)


def validate_ticket_class_stock(
    db: Session,
    ticket_class_id: int,
    new_stock: int,
):
    minimum_stock = get_minimum_ticket_class_stock(
        db,
        ticket_class_id,
    )

    if new_stock < minimum_stock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Stok kelas tiket tidak boleh kurang dari "
                "jumlah tiket terjual dan yang sedang "
                f"direservasi ({minimum_stock} tiket)."
            ),
        )


# =========================================================
# HELPER KELAS TIKET UNTUK APPROVAL
# =========================================================

def validate_ticket_class_data(data: dict):
    unknown_fields = set(data.keys()) - ALLOWED_TICKET_FIELDS

    if unknown_fields:
        raise HTTPException(
            status_code=422,
            detail=(
                "Field kelas tiket tidak diizinkan: "
                + ", ".join(sorted(unknown_fields))
            ),
        )

    if "name" in data:
        if not isinstance(data["name"], str):
            raise HTTPException(
                status_code=422,
                detail="Nama kelas tiket harus berupa teks.",
            )

        data["name"] = data["name"].strip()

        if not data["name"]:
            raise HTTPException(
                status_code=422,
                detail="Nama kelas tiket tidak boleh kosong.",
            )

        if len(data["name"]) > 30:
            raise HTTPException(
                status_code=422,
                detail="Nama kelas tiket maksimal 30 karakter.",
            )

    if "price" in data:
        try:
            price = Decimal(str(data["price"]))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Harga kelas tiket tidak valid.",
            )

        if not price.is_finite() or price < 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Harga kelas tiket tidak boleh negatif "
                    "atau tidak valid."
                ),
            )

        data["price"] = price

    if "available_tickets" in data:
        try:
            raw_stock = data["available_tickets"]

            if isinstance(raw_stock, bool):
                raise ValueError

            stock = int(raw_stock)

            if str(raw_stock).strip() != str(stock):
                raise ValueError

        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Stok kelas tiket harus berupa bilangan bulat.",
            )

        if stock < 0:
            raise HTTPException(
                status_code=422,
                detail="Stok kelas tiket tidak boleh negatif.",
            )

        data["available_tickets"] = stock

    return data


def ensure_unique_ticket_class_name(
    db: Session,
    event_id: int,
    name: str,
    exclude_id: int | None = None,
):
    query = select(TicketClass).where(
        TicketClass.event_id == event_id,
        func.lower(TicketClass.name) == name.lower(),
    )

    if exclude_id is not None:
        query = query.where(
            TicketClass.id != exclude_id
        )

    existing = db.scalar(query)

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nama kelas tiket sudah digunakan pada event ini.",
        )


def apply_ticket_class_changes(
    db: Session,
    event: Event,
    ticket_operations: list,
):
    """
    Menjalankan operasi kelas tiket dalam transaksi approval.
    """

    if not isinstance(ticket_operations, list):
        raise HTTPException(
            status_code=422,
            detail="Format perubahan kelas tiket tidak valid.",
        )

    for operation in ticket_operations:
        if not isinstance(operation, dict):
            raise HTTPException(
                status_code=422,
                detail="Format operasi kelas tiket tidak valid.",
            )

        action = operation.get("action")

        if action not in ("create", "update", "delete"):
            raise HTTPException(
                status_code=422,
                detail="Operasi kelas tiket tidak dikenal.",
            )

        if action == "create":
            ticket_data = operation.get("data")

            if not isinstance(ticket_data, dict):
                raise HTTPException(
                    status_code=422,
                    detail="Data kelas tiket baru tidak valid.",
                )

            ticket_data = validate_ticket_class_data(
                dict(ticket_data)
            )

            required_fields = {
                "name",
                "price",
                "available_tickets",
            }

            if not required_fields.issubset(ticket_data):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Nama, harga, dan stok wajib diisi "
                        "untuk kelas tiket baru."
                    ),
                )

            ensure_unique_ticket_class_name(
                db,
                event.id,
                ticket_data["name"],
            )

            ticket_class = TicketClass(
                event_id=event.id,
                name=ticket_data["name"],
                price=ticket_data["price"],
                stock=ticket_data["available_tickets"],
            )

            db.add(ticket_class)

        else:
            ticket_class_id = operation.get(
                "ticket_class_id"
            )

            if ticket_class_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="ID kelas tiket wajib diisi.",
                )

            ticket_class = db.scalar(
                select(TicketClass)
                .where(
                    TicketClass.id == ticket_class_id,
                    TicketClass.event_id == event.id,
                )
                .with_for_update()
            )

            if ticket_class is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Kelas tiket tidak ditemukan pada event ini.",
                )

            if action == "update":
                ticket_data = operation.get("data")

                if not isinstance(ticket_data, dict):
                    raise HTTPException(
                        status_code=422,
                        detail="Data perubahan kelas tiket tidak valid.",
                    )

                ticket_data = validate_ticket_class_data(
                    dict(ticket_data)
                )

                if not ticket_data:
                    raise HTTPException(
                        status_code=422,
                        detail="Tidak ada perubahan kelas tiket.",
                    )

                if "name" in ticket_data:
                    ensure_unique_ticket_class_name(
                        db,
                        event.id,
                        ticket_data["name"],
                        exclude_id=ticket_class.id,
                    )

                if "available_tickets" in ticket_data:
                    validate_ticket_class_stock(
                        db,
                        ticket_class.id,
                        ticket_data["available_tickets"],
                    )

                for key, value in ticket_data.items():
                    if key == "available_tickets":
                        ticket_class.stock = value
                    else:
                        setattr(ticket_class, key, value)

            elif action == "delete":
                existing_order = db.scalar(
                    select(Order.id)
                    .where(
                        Order.ticket_class_id == ticket_class.id
                    )
                    .limit(1)
                )

                if existing_order is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Kelas tiket tidak dapat dihapus karena "
                            "sudah memiliki riwayat pesanan. "
                            "Pertahankan kelas tiket tersebut "
                            "untuk menjaga histori transaksi."
                        ),
                    )

                db.delete(ticket_class)

# =========================================================
# UPLOAD GAMBAR EVENT
# POST /events/upload-image
# =========================================================

@router.post("/upload-image")
async def upload_event_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_event_admin),
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        await file.close()

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Format poster harus JPG, PNG, atau WEBP.",
        )

    extension = ALLOWED_IMAGE_TYPES[file.content_type]

    filename = f"{uuid.uuid4().hex}{extension}"
    destination = EVENT_UPLOAD_DIR / filename

    size = 0

    try:
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File poster kosong.",
            )

        return {
            "message": "Poster berhasil diunggah.",
            "image_url": f"/uploads/events/{filename}",
        }

    except HTTPException:
        destination.unlink(missing_ok=True)
        raise

    except OSError:
        logger.exception(
            "Gagal menyimpan poster event."
        )

        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal menyimpan poster.",
        )

    finally:
        await file.close()


# =========================================================
# GET SEMUA EVENT PUBLIK
# GET /events/
# =========================================================

@router.get(
    "/",
    response_model=list[EventResponse],
)
def get_events(
    db: Session = Depends(get_db),
):
    try:
        return db.scalars(
            select(Event)
            .where(
                Event.approval_status == "approved",
                Event.is_cancelled.is_(False),
                Event.is_deleted.is_(False),
            )
            .order_by(Event.event_date.asc())
        ).all()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil daftar event."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil daftar event.",
        )


# =========================================================
# GET EVENT MILIK ADMIN
# GET /events/my
# =========================================================

@router.get(
    "/my",
    response_model=list[EventResponse],
)
def get_my_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        query = select(Event).where(
            Event.is_deleted.is_(False)
        )

        if current_user.role == "admin_event":
            query = query.where(
                Event.created_by == current_user.id
            )

        return db.scalars(
            query.order_by(Event.created_at.desc())
        ).all()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil event yang dikelola user: user_id=%s",
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil daftar event yang dikelola.",
        )


# =========================================================
# GET EVENT PENDING
# GET /events/admin/pending
# =========================================================

@router.get(
    "/admin/pending",
    response_model=list[EventResponse],
)
def get_pending_events(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return db.scalars(
            select(Event)
            .where(
                Event.approval_status == "pending",
                Event.is_deleted.is_(False),
            )
            .order_by(Event.created_at.desc())
        ).all()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil event pending."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar event pending gagal dimuat.",
        )


# =========================================================
# GET UPDATE REQUESTS
# GET /events/admin/update-requests
# =========================================================

@router.get(
    "/admin/update-requests",
    response_model=list[EventUpdateRequestResponse],
)
def get_event_update_requests(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return db.scalars(
            select(EventUpdateRequest)
            .order_by(EventUpdateRequest.created_at.desc())
        ).all()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil pengajuan perubahan event."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pengajuan perubahan gagal dimuat.",
        )


# =========================================================
# REVIEW UPDATE REQUEST
# POST /events/admin/update-requests/{request_id}/review
# =========================================================

@router.post(
    "/admin/update-requests/{request_id}/review",
)
def review_event_update_request(
    request_id: int,
    data: EventUpdateRequestReview,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    try:
        request = db.scalar(
            select(EventUpdateRequest)
            .where(EventUpdateRequest.id == request_id)
            .with_for_update()
        )

        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pengajuan perubahan tidak ditemukan.",
            )

        if request.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pengajuan ini sudah diproses.",
            )

        event = db.scalar(
            select(Event)
            .where(Event.id == request.event_id)
            .with_for_update()
        )

        if event is None or event.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan atau sudah dihapus.",
            )

        if event.created_by != request.requested_by:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pengaju bukan pemilik event.",
            )

        if data.status == "approved":

            if event.is_cancelled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Event yang dibatalkan tidak dapat diperbarui.",
                )

            if event.approval_status != "approved":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Perubahan hanya dapat diterapkan pada "
                        "event yang sudah disetujui."
                    ),
                )

            proposed_data = request.proposed_data or {}

            if not isinstance(proposed_data, dict):
                raise HTTPException(
                    status_code=422,
                    detail="Format pengajuan perubahan tidak valid.",
                )

            event_changes = proposed_data.get("event", {})

            ticket_operations = proposed_data.get(
                "ticket_classes",
                [],
            )

            if not isinstance(event_changes, dict):
                raise HTTPException(
                    status_code=422,
                    detail="Data perubahan event tidak valid.",
                )

            unknown_fields = (
                set(event_changes.keys()) - ALLOWED_EVENT_FIELDS
            )

            if unknown_fields:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Pengajuan berisi field event "
                        "yang tidak diizinkan."
                    ),
                )

            event_changes = normalize_event_dates(
                dict(event_changes)
            )

            event_changes = validate_event_data(
                event_changes
            )

            validate_event_dates(
                event_changes,
                event,
            )

            if "available_tickets" in event_changes:
                validate_event_stock(
                    db,
                    event.id,
                    event_changes["available_tickets"],
                )

            for key, value in event_changes.items():
                setattr(event, key, value)

            apply_ticket_class_changes(
                db,
                event,
                ticket_operations,
            )

        request.status = data.status
        request.reviewed_by = current_admin.id
        request.reviewed_at = datetime.now(timezone.utc)

        request.rejection_reason = (
            data.rejection_reason.strip()
            if data.status == "rejected"
            else None
        )

        db.commit()

        return {
            "message": (
                "Perubahan event dan kelas tiket berhasil disetujui."
                if data.status == "approved"
                else "Pengajuan perubahan event ditolak."
            ),
            "request_id": request.id,
            "status": request.status,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal meninjau perubahan event: request_id=%s",
            request_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pengajuan perubahan event gagal diproses.",
        )


# =========================================================
# AJUKAN PENGHAPUSAN EVENT
# DELETE /events/{event_id}
# =========================================================

@router.delete("/{event_id}")
def request_event_deletion(
    event_id: int,
    data: EventDeletionRequestCreate | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        event = db.scalar(
            select(Event)
            .where(
                Event.id == event_id,
                Event.is_deleted.is_(False),
            )
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        ensure_event_owner(
            event,
            current_user,
        )

        # SUPER ADMIN: SOFT DELETE LANGSUNG
        if current_user.role == "admin":
            event.is_deleted = True
            event.deleted_at = datetime.now(timezone.utc)
            event.deleted_by = current_user.id

            db.commit()

            return {
                "message": "Event berhasil dihapus.",
                "event_id": event.id,
                "is_deleted": True,
            }

        # ADMIN EVENT: AJUKAN PENGHAPUSAN
        if event.approval_status != "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Hanya event yang sudah disetujui "
                    "yang dapat diajukan penghapusannya."
                ),
            )

        existing_request = db.scalar(
            select(EventDeletionRequest)
            .where(
                EventDeletionRequest.event_id == event_id,
                EventDeletionRequest.status == "pending",
            )
            .with_for_update()
        )

        if existing_request:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Pengajuan penghapusan event masih "
                    "menunggu persetujuan."
                ),
            )

        request = EventDeletionRequest(
            event_id=event.id,
            requested_by=current_user.id,
            reason=(
                data.reason.strip()
                if data and data.reason
                else None
            ),
            status="pending",
        )

        db.add(request)
        db.commit()
        db.refresh(request)

        return request

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengajukan penghapusan event: event_id=%s",
            event_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pengajuan penghapusan event gagal.",
        )


# =========================================================
# GET DELETION REQUESTS
# GET /events/admin/deletion-requests
# =========================================================

@router.get(
    "/admin/deletion-requests",
    response_model=list[EventDeletionRequestResponse],
)
def get_event_deletion_requests(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return db.scalars(
            select(EventDeletionRequest)
            .order_by(EventDeletionRequest.created_at.desc())
        ).all()

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil pengajuan penghapusan event."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar pengajuan penghapusan gagal dimuat.",
        )


# =========================================================
# REVIEW DELETION REQUEST
# POST /events/admin/deletion-requests/{request_id}/review
# =========================================================

@router.post(
    "/admin/deletion-requests/{request_id}/review",
)
def review_event_deletion_request(
    request_id: int,
    data: EventDeletionRequestReview,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    try:
        request = db.scalar(
            select(EventDeletionRequest)
            .where(EventDeletionRequest.id == request_id)
            .with_for_update()
        )

        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pengajuan penghapusan tidak ditemukan.",
            )

        if request.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pengajuan ini sudah diproses.",
            )

        event = db.scalar(
            select(Event)
            .where(Event.id == request.event_id)
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        if event.created_by != request.requested_by:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pengaju bukan pemilik event.",
            )

        if data.status == "approved":
            event.is_deleted = True
            event.deleted_at = datetime.now(timezone.utc)
            event.deleted_by = current_admin.id

        request.status = data.status
        request.reviewed_by = current_admin.id
        request.reviewed_at = datetime.now(timezone.utc)

        request.rejection_reason = (
            data.rejection_reason.strip()
            if data.status == "rejected"
            else None
        )

        db.commit()

        return {
            "message": (
                "Penghapusan event berhasil disetujui."
                if data.status == "approved"
                else "Pengajuan penghapusan event ditolak."
            ),
            "event_id": event.id,
            "is_deleted": event.is_deleted,
            "request_id": request.id,
            "status": request.status,
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal meninjau penghapusan event: request_id=%s",
            request_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pengajuan penghapusan event gagal diproses.",
        )


# =========================================================
# CANCEL EVENT — SUPER ADMIN
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
            .where(
                Event.id == event_id,
                Event.is_deleted.is_(False),
            )
            .with_for_update()
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        if event.is_cancelled:
            return {
                "message": "Event sudah dibatalkan.",
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

            if order.stock_reserved:
                ticket_class = db.scalar(
                    select(TicketClass)
                    .where(
                        TicketClass.id == order.ticket_class_id,
                        TicketClass.event_id == event_id,
                    )
                    .with_for_update()
                )

                if ticket_class is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Kelas tiket pesanan tidak ditemukan. "
                            "Pembatalan event dibatalkan."
                        ),
                    )

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

        logger.exception(
            "Gagal membatalkan event: event_id=%s",
            event_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal dibatalkan.",
        )


# =========================================================
# GET DETAIL EVENT PUBLIK
# GET /events/{event_id}
# LETAKKAN DI BAGIAN PALING BAWAH
# =========================================================

@router.get(
    "/{event_id}",
    response_model=EventResponse,
)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    try:
        event = db.scalar(
            select(Event).where(
                Event.id == event_id,
                Event.approval_status == "approved",
                Event.is_cancelled.is_(False),
                Event.is_deleted.is_(False),
            )
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event tidak ditemukan.",
            )

        return event

    except HTTPException:
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil detail event: event_id=%s",
            event_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal mengambil detail event.",
        )
