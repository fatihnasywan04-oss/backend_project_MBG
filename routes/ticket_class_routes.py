from decimal import Decimal

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Event, TicketClass, User, Order
from routes.auth_routes import get_current_user


router = APIRouter(
    prefix="/events/{event_id}/ticket-classes",
    tags=["Ticket Classes"],
)


# =========================================================
# ADMIN AUTHORIZATION
# =========================================================

def require_admin(
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )

    return current_user


# =========================================================
# REQUEST SCHEMAS
# =========================================================

class TicketClassCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=30,
    )

    price: Decimal = Field(
        ge=0,
    )

    available_tickets: int = Field(
        ge=0,
    )


class TicketClassUpdate(BaseModel):
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


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_event_or_404(
    event_id: int,
    db: Session,
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event tidak ditemukan",
        )

    if event.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event sudah dibatalkan",
        )

    return event


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
            detail="Kelas tiket tidak ditemukan",
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
            detail="Nama kelas tiket wajib diisi",
        )

    if len(name) > 30:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nama kelas tiket maksimal 30 karakter",
        )

    return name


# =========================================================
# GET ALL TICKET CLASSES
# =========================================================

@router.get("")
@router.get("/")
def get_ticket_classes(
    event_id: int,
    db: Session = Depends(get_db),
):
    get_event_or_404(event_id, db)

    ticket_classes = db.scalars(
        select(TicketClass)
        .where(TicketClass.event_id == event_id)
        .order_by(TicketClass.id.asc())
    ).all()

    return [
        serialize_ticket_class(item)
        for item in ticket_classes
    ]


# =========================================================
# CREATE TICKET CLASS
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
    admin: User = Depends(require_admin),
):
    get_event_or_404(event_id, db)

    name = validate_ticket_class_name(data.name)

    duplicate = db.scalar(
        select(TicketClass).where(
            TicketClass.event_id == event_id,
            TicketClass.name == name,
        )
    )

    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nama kelas tiket tersebut sudah digunakan",
        )

    ticket_class = TicketClass(
        event_id=event_id,
        name=name,
        price=data.price,
        stock=data.available_tickets,
    )

    try:
        db.add(ticket_class)
        db.commit()
        db.refresh(ticket_class)

        return serialize_ticket_class(ticket_class)

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal dibuat",
        )


# =========================================================
# UPDATE TICKET CLASS
# =========================================================

@router.put("/{ticket_class_id}")
def update_ticket_class(
    event_id: int,
    ticket_class_id: int,
    data: TicketClassUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    get_event_or_404(event_id, db)

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
            detail="Tidak ada data yang diubah",
        )

    if "name" in changes:
        name = validate_ticket_class_name(
            changes["name"] or ""
        )

        duplicate = db.scalar(
            select(TicketClass).where(
                TicketClass.event_id == event_id,
                TicketClass.name == name,
                TicketClass.id != ticket_class_id,
            )
        )

        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Nama kelas tiket tersebut sudah digunakan",
            )

        ticket_class.name = name

    if "price" in changes:
        ticket_class.price = changes["price"]

    if "available_tickets" in changes:
        ticket_class.stock = changes["available_tickets"]

    try:
        db.commit()
        db.refresh(ticket_class)

        return serialize_ticket_class(ticket_class)

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal diperbarui",
        )


# =========================================================
# DELETE TICKET CLASS
# =========================================================

@router.delete("/{ticket_class_id}")
def delete_ticket_class(
    event_id: int,
    ticket_class_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    get_event_or_404(event_id, db)

    ticket_class = get_ticket_class_or_404(
        event_id=event_id,
        ticket_class_id=ticket_class_id,
        db=db,
        lock=True,
    )

    existing_order = db.scalar(
        select(Order.id)
        .where(
            Order.ticket_class_id == ticket_class_id
        )
        .limit(1)
    )

    if existing_order is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Kelas tiket sudah memiliki pesanan dan tidak bisa "
                "dihapus permanen. Ubah nama atau hentikan penjualannya."
            ),
        )

    try:
        db.delete(ticket_class)
        db.commit()

        return {
            "message": "Kelas tiket berhasil dihapus"
        }

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Kelas tiket gagal dihapus",
        )