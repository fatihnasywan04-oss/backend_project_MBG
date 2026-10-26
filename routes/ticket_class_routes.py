
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import Event, TicketClass, User
from routes.auth_routes import get_current_user


router = APIRouter(
    prefix="/events",
    tags=["Ticket Classes"],
)


# =========================
# RESPONSE KELAS TIKET
# =========================

class TicketClassResponse(BaseModel):
    id: int
    event_id: int
    name: str
    price: Decimal
    available_tickets: int

    model_config = ConfigDict(from_attributes=True)


# =========================
# REQUEST UPDATE KELAS TIKET
# =========================

class TicketClassUpdate(BaseModel):
    price: Decimal = Field(ge=0)
    available_tickets: int = Field(ge=0)


# =========================
# VALIDASI ADMIN
# =========================

def require_admin(user: User):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Hanya admin yang boleh mengubah harga dan stok tiket",
        )


# =========================
# GET KELAS TIKET BERDASARKAN EVENT
# GET /events/{event_id}/ticket-classes
# =========================

@router.get(
    "/{event_id}/ticket-classes",
    response_model=list[TicketClassResponse],
)
def get_ticket_classes(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event tidak ditemukan",
        )

    ticket_classes = db.scalars(
        select(TicketClass)
        .where(TicketClass.event_id == event_id)
        .order_by(TicketClass.price.desc())
    ).all()

    return ticket_classes


# =========================
# UPDATE HARGA DAN STOK
# PUT /events/{event_id}/ticket-classes/{ticket_class_id}
# KHUSUS ADMIN
# =========================

@router.put(
    "/{event_id}/ticket-classes/{ticket_class_id}",
    response_model=TicketClassResponse,
)
def update_ticket_class(
    event_id: int,
    ticket_class_id: int,
    data: TicketClassUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    ticket_class = db.scalar(
        select(TicketClass)
        .where(
            TicketClass.id == ticket_class_id,
            TicketClass.event_id == event_id,
        )
        .with_for_update()
    )

    if ticket_class is None:
        raise HTTPException(
            status_code=404,
            detail="Kelas tiket tidak ditemukan untuk event ini",
        )

    ticket_class.price = data.price
    ticket_class.available_tickets = data.available_tickets

    try:
        db.commit()
        db.refresh(ticket_class)
        return ticket_class

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Gagal memperbarui harga dan stok tiket",
        )
