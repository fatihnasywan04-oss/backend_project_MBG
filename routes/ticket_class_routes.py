
import logging
from decimal import Decimal

from pydantic import BaseModel, Field, ConfigDict, field_validator
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Event,
    TicketClass,
    User,
    Order,
    EventUpdateRequest,
)
from routes.auth_routes import get_current_user


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/events/{event_id}/ticket-classes",
    tags=["Ticket Classes"],
)


# =========================================================
# REQUEST SCHEMAS
# =========================================================

class TicketClassCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=30)
    price: Decimal = Field(ge=0)
    available_tickets: int = Field(ge=0)

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal):
        if not value.is_finite():
            raise ValueError("Harga harus berupa angka yang valid.")

        if value < 0:
            raise ValueError("Harga tidak boleh negatif.")

        return value

    @field_validator("available_tickets")
    @classmethod
    def validate_stock(cls, value: int):
        if isinstance(value, bool):
            raise ValueError("Stok harus berupa bilangan bulat.")

        if value < 0:
            raise ValueError("Stok tidak boleh negatif.")

        return value


class TicketClassUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
    )

    price: Decimal | None = Field(
        default=None,
        ge=0,
    )

    available_tickets: int | None = Field(
        default=None,
        ge=0,
    )

    @field_validator("price")
    @classmethod
    def validate_price(cls, value):
        if value is None:
            return value

        if not value.is_finite():
            raise ValueError("Harga harus berupa angka yang valid.")

        if value < 0:
            raise ValueError("Harga tidak boleh negatif.")

        return value

    @field_validator("available_tickets")
    @classmethod
    def validate_stock(cls, value):
        if value is None:
            return value

        if isinstance(value, bool):
            raise ValueError("Stok harus berupa bilangan bulat.")

        if value < 0:
            raise ValueError("Stok tidak boleh negatif.")

        return value


# =========================================================
# AUTHORIZATION
# =========================================================

def require_event_admin(
    current_user: User = Depends(get_current_user),
):
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


# =========================================================
# HELPER EVENT
# =========================================================

def get_event_or_404(
    event_id: int,
    db: Session,
    current_user: User,
    lock: bool = False,
    allow_pending: bool = False,
):
    query = select(Event).where(
        Event.id == event_id,
        Event.is_deleted.is_(False),
    )

    if lock:
        query = query.with_for_update()

    event = db.scalar(query)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event tidak ditemukan.",
        )

    if (
        current_user.role == "admin_event"
        and event.created_by != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Anda hanya dapat mengelola event milik sendiri.",
        )

    if event.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event sudah dibatalkan.",
        )

    if event.approval_status != "approved":
        if not (
            allow_pending
            and event.approval_status == "pending"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Event belum disetujui atau tidak dapat "
                    "dikelola."
                ),
            )

    return event


# =========================================================
# HELPER TICKET CLASS
# =========================================================

def get_ticket_class_or_404(
    event_id: int,
    ticket_class_id: int,
    db: Session,
    lock: bool = False,
):
    query = select(TicketClass).where(
        TicketClass.id == ticket_class_id,
        TicketClass.event_id == event_id,
    )

    if lock:
        query = query.with_for_update()

    ticket_class = db.scalar(query)

    if ticket_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kelas tiket tidak ditemukan.",
        )

    return ticket_class


def serialize_ticket_class(
    ticket_class: TicketClass,
):
    return {
        "id": ticket_class.id,
        "event_id": ticket_class.event_id,
        "name": ticket_class.name,
        "price": ticket_class.price,
        "available_tickets": ticket_class.stock,
    }


def validate_ticket_class_name(name: str):
    name = name.strip()

    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nama kelas tiket wajib diisi.",
        )

    if len(name) > 30:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nama kelas tiket maksimal 30 karakter.",
        )

    return name


def check_duplicate_name(
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

    duplicate = db.scalar(query)

    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nama kelas tiket tersebut sudah digunakan.",
        )


# =========================================================
# VALIDASI STOK
# =========================================================

def get_minimum_ticket_stock(
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


def validate_ticket_stock(
    db: Session,
    ticket_class_id: int,
    new_stock: int,
):
    minimum_stock = get_minimum_ticket_stock(
        db,
        ticket_class_id,
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


# =========================================================
# VALIDASI RIWAYAT ORDER
# =========================================================

def ensure_ticket_class_has_no_orders(
    db: Session,
    ticket_class_id: int,
):
    existing_order = db.scalar(
        select(Order.id)
        .where(
            Order.ticket_class_id == ticket_class_id,
        )
        .limit(1)
    )

    if existing_order is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Kelas tiket sudah memiliki riwayat pesanan "
                "dan tidak bisa dihapus. Ubah stok menjadi 0 "
                "atau hentikan penjualannya."
            ),
        )


# =========================================================
# PENGAJUAN PERUBAHAN
# =========================================================

def create_change_request(
    db: Session,
    event_id: int,
    user_id: int,
    operation: dict,
):
    """
    Menyimpan perubahan kelas tiket sebagai EventUpdateRequest.

    Perubahan untuk event approved belum diterapkan langsung.
    Perubahan menunggu persetujuan Super Admin.
    """

    request = EventUpdateRequest(
        event_id=event_id,
        requested_by=user_id,
        proposed_data={
            "event": {},
            "ticket_classes": [operation],
        },
        status="pending",
    )

    db.add(request)
    db.commit()
    db.refresh(request)

    return request


# =========================================================
# GET ALL TICKET CLASSES - PUBLIK
# GET /events/{event_id}/ticket-classes
# =========================================================

@router.get("")
@router.get("/")
def get_ticket_classes(
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

        ticket_classes = db.scalars(
            select(TicketClass)
            .where(
                TicketClass.event_id == event_id,
            )
            .order_by(TicketClass.id.asc())
        ).all()

        return [
            serialize_ticket_class(item)
            for item in ticket_classes
        ]

    except HTTPException:
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil kelas tiket: event_id=%s",
            event_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar kelas tiket gagal dimuat.",
        )


# =========================================================
# GET TICKET CLASSES - MANAJEMEN ADMIN
# GET /events/{event_id}/ticket-classes/manage
# =========================================================

@router.get("/manage")
def get_managed_ticket_classes(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        get_event_or_404(
            event_id=event_id,
            db=db,
            current_user=current_user,
            allow_pending=True,
        )

        ticket_classes = db.scalars(
            select(TicketClass)
            .where(
                TicketClass.event_id == event_id,
            )
            .order_by(TicketClass.id.asc())
        ).all()

        return [
            serialize_ticket_class(item)
            for item in ticket_classes
        ]

    except HTTPException:
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal mengambil kelas tiket untuk manajemen: "
            "event_id=%s, user_id=%s",
            event_id,
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Daftar kelas tiket gagal dimuat.",
        )

# =========================================================
# CREATE TICKET CLASS
# POST /events/{event_id}/ticket-classes
# =========================================================

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
)
def create_ticket_class(
    event_id: int,
    data: TicketClassCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        event = get_event_or_404(
            event_id,
            db,
            current_user,
            lock=True,
            allow_pending=True,
        )

        name = validate_ticket_class_name(data.name)

        check_duplicate_name(
            db,
            event_id,
            name,
        )

        # EVENT MASIH PENDING:
        # Kelas tiket disimpan langsung karena belum tersedia
        # untuk pembelian publik.
        if event.approval_status == "pending":
            ticket_class = TicketClass(
                event_id=event_id,
                name=name,
                price=data.price,
                stock=data.available_tickets,
            )

            db.add(ticket_class)
            db.commit()
            db.refresh(ticket_class)

            return {
                "message": (
                    "Kelas tiket berhasil ditambahkan. "
                    "Kelas tiket akan tersedia setelah event "
                    "disetujui Super Admin."
                ),
                "status": "pending",
                "ticket_class": serialize_ticket_class(
                    ticket_class
                ),
            }

        # EVENT SUDAH APPROVED:
        # Siapkan operasi untuk pengajuan perubahan.
        operation = {
            "action": "create",
            "ticket_class_id": None,
            "data": {
                "name": name,
                "price": str(data.price),
                "available_tickets": data.available_tickets,
            },
        }

        if current_user.role == "admin_event":
            request = create_change_request(
                db,
                event_id,
                current_user.id,
                operation,
            )

            return {
                "message": (
                    "Pengajuan penambahan kelas tiket berhasil "
                    "dikirim dan menunggu persetujuan Super Admin."
                ),
                "request_id": request.id,
                "status": request.status,
            }

        # SUPER ADMIN: SIMPAN LANGSUNG
        ticket_class = TicketClass(
            event_id=event_id,
            name=name,
            price=data.price,
            stock=data.available_tickets,
        )

        db.add(ticket_class)
        db.commit()
        db.refresh(ticket_class)

        return serialize_ticket_class(ticket_class)

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal membuat kelas tiket: event_id=%s, user_id=%s",
            event_id,
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal dibuat.",
        )


# =========================================================
# UPDATE TICKET CLASS
# PUT /events/{event_id}/ticket-classes/{ticket_class_id}
# =========================================================

@router.put("/{ticket_class_id}")
def update_ticket_class(
    event_id: int,
    ticket_class_id: int,
    data: TicketClassUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        event = get_event_or_404(
            event_id,
            db,
            current_user,
            lock=True,
            allow_pending=True,
        )

        ticket_class = get_ticket_class_or_404(
            event_id=event_id,
            ticket_class_id=ticket_class_id,
            db=db,
            lock=True,
        )

        changes = data.model_dump(
            exclude_unset=True,
        )

        if not changes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tidak ada data yang diubah.",
            )

        for key, value in changes.items():
            if value is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Field {key} tidak boleh bernilai null.",
                )

        if "name" in changes:
            name = validate_ticket_class_name(
                changes["name"]
            )

            check_duplicate_name(
                db,
                event_id,
                name,
                exclude_id=ticket_class_id,
            )

            changes["name"] = name

        if "available_tickets" in changes:
            validate_ticket_stock(
                db,
                ticket_class_id,
                changes["available_tickets"],
            )

        # EVENT PENDING:
        # Perubahan dapat diterapkan langsung.
        if event.approval_status == "pending":
            if "name" in changes:
                ticket_class.name = changes["name"]

            if "price" in changes:
                ticket_class.price = changes["price"]

            if "available_tickets" in changes:
                ticket_class.stock = changes["available_tickets"]

            db.commit()
            db.refresh(ticket_class)

            return {
                "message": (
                    "Kelas tiket berhasil diperbarui. "
                    "Perubahan akan ikut dalam proses "
                    "persetujuan event."
                ),
                "status": "pending",
                "ticket_class": serialize_ticket_class(
                    ticket_class
                ),
            }

        operation = {
            "action": "update",
            "ticket_class_id": ticket_class.id,
            "data": {
                key: (
                    str(value)
                    if isinstance(value, Decimal)
                    else value
                )
                for key, value in changes.items()
            },
        }

        # ADMIN EVENT PADA EVENT APPROVED:
        # Perubahan harus disetujui Super Admin.
        if current_user.role == "admin_event":
            request = create_change_request(
                db,
                event_id,
                current_user.id,
                operation,
            )

            return {
                "message": (
                    "Perubahan kelas tiket berhasil diajukan "
                    "dan menunggu persetujuan Super Admin."
                ),
                "request_id": request.id,
                "status": request.status,
            }

        # SUPER ADMIN: PERUBAHAN LANGSUNG
        if "name" in changes:
            ticket_class.name = changes["name"]

        if "price" in changes:
            ticket_class.price = changes["price"]

        if "available_tickets" in changes:
            ticket_class.stock = changes["available_tickets"]

        db.commit()
        db.refresh(ticket_class)

        return serialize_ticket_class(ticket_class)

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal memperbarui kelas tiket: "
            "event_id=%s, ticket_class_id=%s",
            event_id,
            ticket_class_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal diperbarui.",
        )


# =========================================================
# DELETE TICKET CLASS
# DELETE /events/{event_id}/ticket-classes/{ticket_class_id}
# =========================================================

@router.delete("/{ticket_class_id}")
def delete_ticket_class(
    event_id: int,
    ticket_class_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_event_admin),
):
    try:
        event = get_event_or_404(
            event_id,
            db,
            current_user,
            lock=True,
            allow_pending=True,
        )

        ticket_class = get_ticket_class_or_404(
            event_id=event_id,
            ticket_class_id=ticket_class_id,
            db=db,
            lock=True,
        )

        # Kelas tiket yang memiliki riwayat order tidak boleh
        # dihapus, termasuk jika order sudah dibatalkan.
        ensure_ticket_class_has_no_orders(
            db,
            ticket_class_id,
        )

        # EVENT PENDING:
        # Penghapusan langsung karena belum ada penjualan publik.
        if event.approval_status == "pending":
            db.delete(ticket_class)
            db.commit()

            return {
                "message": (
                    "Kelas tiket berhasil dihapus dari "
                    "event yang masih menunggu persetujuan."
                ),
                "status": "pending",
            }

        operation = {
            "action": "delete",
            "ticket_class_id": ticket_class.id,
            "data": {},
        }

        # ADMIN EVENT PADA EVENT APPROVED:
        # Penghapusan harus menunggu persetujuan Super Admin.
        if current_user.role == "admin_event":
            request = create_change_request(
                db,
                event_id,
                current_user.id,
                operation,
            )

            return {
                "message": (
                    "Pengajuan penghapusan kelas tiket berhasil "
                    "dikirim dan menunggu persetujuan Super Admin."
                ),
                "request_id": request.id,
                "status": request.status,
            }

        # SUPER ADMIN: HAPUS LANGSUNG
        db.delete(ticket_class)
        db.commit()

        return {
            "message": "Kelas tiket berhasil dihapus.",
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()

        logger.exception(
            "Gagal menghapus kelas tiket: "
            "event_id=%s, ticket_class_id=%s",
            event_id,
            ticket_class_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal dihapus.",
        )
