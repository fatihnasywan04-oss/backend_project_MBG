from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import get_db
from models import Event, User, Order
from schemas import EventCreate, EventUpdate, EventResponse
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/events", tags=["Events"])


# =========================
# ADMIN AUTHORIZATION
# =========================

def require_admin(
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )

    return current_user


# =========================
# READ: SEMUA EVENT
# =========================

@router.get("/", response_model=list[EventResponse])
def get_events(db: Session = Depends(get_db)):
    return db.scalars(
        select(Event).order_by(Event.event_date.asc())
    ).all()


# =========================
# READ: DETAIL EVENT
# =========================

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


# =========================
# CREATE: TAMBAH EVENT
# ADMIN SAJA
# =========================

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

    if data.image_url is not None:
        event_data["image_url"] = str(data.image_url)

    event = Event(
        **event_data,
        created_by=admin.id,
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


# =========================
# UPDATE: EDIT EVENT
# ADMIN SAJA
# =========================

@router.put(
    "/{event_id}",
    response_model=EventResponse,
)
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event tidak ditemukan",
        )

    update_data = data.model_dump(exclude_unset=True)

    if "image_url" in update_data:
        image_url = update_data["image_url"]
        update_data["image_url"] = (
            str(image_url) if image_url is not None else None
        )

    # Kolom wajib tidak boleh diubah menjadi null.
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

        setattr(event, key, value)

    try:
        db.commit()
        db.refresh(event)

        return event

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal diperbarui",
        )


# =========================
# DELETE: HAPUS EVENT
# ADMIN SAJA
# =========================

@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
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

    # Cegah penghapusan event yang sudah memiliki pesanan.
    existing_order = db.scalar(
        select(Order.id)
        .where(Order.event_id == event_id)
        .limit(1)
    )

    if existing_order is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Event tidak dapat dihapus karena sudah memiliki "
                "pesanan. Edit event jika ingin memperbarui informasinya."
            ),
        )

    try:
        db.delete(event)
        db.commit()

        return {
            "message": "Event berhasil dihapus",
            "event_id": event_id,
        }

    except SQLAlchemyError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Event gagal dihapus",
        )