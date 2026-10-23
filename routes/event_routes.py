from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_db
from models import Event, User
from schemas import EventCreate, EventUpdate, EventResponse
from routes.auth_routes import get_current_user

router = APIRouter(prefix="/events", tags=["Events"])


def require_admin(
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses hanya untuk admin",
        )

    return current_user


# Melihat semua event
@router.get("/", response_model=list[EventResponse])
def get_events(db: Session = Depends(get_db)):
    return db.scalars(
        select(Event).order_by(Event.event_date.asc())
    ).all()


# Melihat detail event
@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    return event


# Menambahkan event - admin saja
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
    event_data["image_url"] = (
        str(data.image_url) if data.image_url else None
    )

    event = Event(
        **event_data,
        created_by=admin.id,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return event


# Mengedit event - admin saja
@router.put("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    update_data = data.model_dump(exclude_unset=True)

    if "image_url" in update_data:
        update_data["image_url"] = (
            str(update_data["image_url"])
            if update_data["image_url"] is not None
            else None
        )

    for key, value in update_data.items():
        setattr(event, key, value)

    db.commit()
    db.refresh(event)

    return event


# Menghapus event - admin saja
@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    event = db.get(Event, event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    db.delete(event)
    db.commit()

    return {"message": "Event berhasil dihapus"}