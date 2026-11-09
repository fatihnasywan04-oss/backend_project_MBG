
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)


# =========================================================
# AUTHENTICATION
# =========================================================

class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str

    # none, pending, approved, rejected
    admin_event_status: str = "none"

    admin_event_reviewed_at: datetime | None = None
    admin_event_rejection_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# =========================================================
# ADMIN EVENT APPLICATION
# =========================================================

class AdminEventApplicationResponse(BaseModel):
    id: int | None = None
    user_id: int
    user_name: str | None = None
    user_email: EmailStr | None = None

    status: str
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminEventApplicationReview(BaseModel):
    status: Literal["approved", "rejected"]

    rejection_reason: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.status == "rejected":
            reason = (self.rejection_reason or "").strip()

            if not reason:
                raise ValueError("Alasan penolakan wajib diisi.")

            self.rejection_reason = reason

        return self


# =========================================================
# TICKET CLASS INPUT UNTUK PEMBUATAN EVENT
# =========================================================

class EventTicketClassCreate(BaseModel):
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

    @model_validator(mode="after")
    def validate_ticket_class(self):
        self.name = self.name.strip()

        if not self.name:
            raise ValueError("Nama kelas tiket wajib diisi.")

        if not self.price.is_finite():
            raise ValueError("Harga kelas tiket tidak valid.")

        if isinstance(self.available_tickets, bool):
            raise ValueError("Stok kelas tiket tidak valid.")

        return self


# =========================================================
# EVENT CREATE
# =========================================================

class EventCreate(BaseModel):
    title: str = Field(
        min_length=3,
        max_length=255,
    )

    description: str = Field(
        min_length=5,
    )

    location: str = Field(
        min_length=2,
        max_length=255,
    )

    category: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )

    instagram_username: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^[A-Za-z0-9._]+$",
    )

    event_date: datetime
    end_date: datetime | None = None

    # Tetap dipertahankan agar request lama tetap kompatibel.
    # Jika ticket_classes dikirim, nilai harga dan stok event
    # akan disesuaikan berdasarkan kelas tiket di event_routes.py.
    price: Decimal = Field(
        ge=0,
    )

    available_tickets: int = Field(
        ge=0,
    )

    image_url: str | None = Field(
        default=None,
        max_length=500,
    )

    # Kelas tiket dikirim bersamaan dengan data event.
    # Admin Event diwajibkan mengirim minimal satu kelas tiket
    # oleh endpoint create_event.
    ticket_classes: list[EventTicketClassCreate] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def validate_event_data(self):
        if self.end_date is not None:

            # Pastikan konsistensi timezone.
            if (
                (self.event_date.tzinfo is None)
                != (self.end_date.tzinfo is None)
            ):
                raise ValueError(
                    "Timezone tanggal mulai dan selesai "
                    "harus konsisten."
                )

            if self.end_date < self.event_date:
                raise ValueError(
                    "Tanggal selesai tidak boleh lebih awal "
                    "dari tanggal mulai event."
                )

        if not self.price.is_finite():
            raise ValueError("Harga event tidak valid.")

        # Validasi nama kelas tiket agar tidak duplikat.
        normalized_names = [
            ticket.name.strip().casefold()
            for ticket in self.ticket_classes
        ]

        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError(
                "Nama kelas tiket tidak boleh duplikat."
            )

        return self


# =========================================================
# EVENT UPDATE
# =========================================================

class EventUpdate(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
    )

    description: str | None = Field(
        default=None,
        min_length=5,
    )

    location: str | None = Field(
        default=None,
        min_length=2,
        max_length=255,
    )

    category: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )

    instagram_username: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^[A-Za-z0-9._]+$",
    )

    event_date: datetime | None = None
    end_date: datetime | None = None

    price: Decimal | None = Field(
        default=None,
        ge=0,
    )

    available_tickets: int | None = Field(
        default=None,
        ge=0,
    )

    image_url: str | None = Field(
        default=None,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_event_dates(self):
        if (
            self.event_date is not None
            and self.end_date is not None
        ):
            if (
                (self.event_date.tzinfo is None)
                != (self.end_date.tzinfo is None)
            ):
                raise ValueError(
                    "Timezone tanggal mulai dan selesai "
                    "harus konsisten."
                )

            if self.end_date < self.event_date:
                raise ValueError(
                    "Tanggal selesai tidak boleh lebih awal "
                    "dari tanggal mulai event."
                )

        if (
            self.price is not None
            and not self.price.is_finite()
        ):
            raise ValueError("Harga event tidak valid.")

        return self


# =========================================================
# EVENT RESPONSE
# =========================================================

class EventResponse(BaseModel):
    id: int

    title: str
    description: str
    location: str

    category: str | None = None
    instagram_username: str | None = None

    event_date: datetime
    end_date: datetime | None = None

    price: Decimal
    available_tickets: int

    image_url: str | None = None

    created_by: int

    # pending, approved, rejected
    approval_status: str = "approved"

    approval_reviewed_by: int | None = None
    approval_reviewed_at: datetime | None = None
    approval_rejection_reason: str | None = None

    is_cancelled: bool = False
    is_deleted: bool = False

    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# EVENT APPROVAL
# =========================================================

class EventApprovalReview(BaseModel):
    status: Literal["approved", "rejected"]

    rejection_reason: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.status == "rejected":
            reason = (self.rejection_reason or "").strip()

            if not reason:
                raise ValueError("Alasan penolakan wajib diisi.")

            self.rejection_reason = reason

        return self

# =========================================================
# EVENT UPDATE REQUEST
# =========================================================

class EventUpdateRequestCreate(BaseModel):
    event: EventUpdate | None = None

    # Operasi perubahan kelas tiket:
    # create, update, delete
    ticket_classes: list[dict[str, Any]] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def validate_request_content(self):
        event_changes = (
            self.event.model_dump(exclude_unset=True)
            if self.event is not None
            else {}
        )

        if not event_changes and not self.ticket_classes:
            raise ValueError(
                "Pengajuan harus memiliki perubahan event "
                "atau perubahan kelas tiket."
            )

        for operation in self.ticket_classes:

            if not isinstance(operation, dict):
                raise ValueError(
                    "Format operasi kelas tiket tidak valid."
                )

            action = operation.get("action")
            ticket_class_id = operation.get("ticket_class_id")
            data = operation.get("data")

            if action not in ["create", "update", "delete"]:
                raise ValueError(
                    "Operasi kelas tiket tidak valid."
                )

            # CREATE
            if action == "create":

                if ticket_class_id is not None:
                    raise ValueError(
                        "ticket_class_id untuk operasi create "
                        "harus bernilai null."
                    )

                if not isinstance(data, dict):
                    raise ValueError(
                        "Data kelas tiket baru harus berupa object."
                    )

                if not data.get("name"):
                    raise ValueError(
                        "Nama kelas tiket wajib diisi."
                    )

                if "price" not in data:
                    raise ValueError(
                        "Harga kelas tiket wajib diisi."
                    )

                if "available_tickets" not in data:
                    raise ValueError(
                        "Stok kelas tiket wajib diisi."
                    )

            # UPDATE
            elif action == "update":

                if (
                    not isinstance(ticket_class_id, int)
                    or isinstance(ticket_class_id, bool)
                    or ticket_class_id <= 0
                ):
                    raise ValueError(
                        "ID kelas tiket tidak valid."
                    )

                if not isinstance(data, dict) or not data:
                    raise ValueError(
                        "Data perubahan kelas tiket wajib diisi."
                    )

            # DELETE
            elif action == "delete":

                if (
                    not isinstance(ticket_class_id, int)
                    or isinstance(ticket_class_id, bool)
                    or ticket_class_id <= 0
                ):
                    raise ValueError(
                        "ID kelas tiket tidak valid."
                    )

                # Operasi delete tidak membutuhkan data.
                # Jika data dikirim, nilainya harus object kosong.
                if data is not None and data != {}:
                    raise ValueError(
                        "Operasi delete tidak membutuhkan "
                        "data perubahan."
                    )

        return self


class EventUpdateRequestResponse(BaseModel):
    id: int
    event_id: int
    requested_by: int

    proposed_data: dict[str, Any]

    status: str

    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventUpdateRequestReview(BaseModel):
    status: Literal["approved", "rejected"]

    rejection_reason: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.status == "rejected":
            reason = (self.rejection_reason or "").strip()

            if not reason:
                raise ValueError("Alasan penolakan wajib diisi.")

            self.rejection_reason = reason

        return self


# =========================================================
# EVENT DELETION REQUEST
# =========================================================

class EventDeletionRequestCreate(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=1000,
    )


class EventDeletionRequestResponse(BaseModel):
    id: int
    event_id: int
    requested_by: int

    reason: str | None = None

    status: str

    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventDeletionRequestReview(BaseModel):
    status: Literal["approved", "rejected"]

    rejection_reason: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.status == "rejected":
            reason = (self.rejection_reason or "").strip()

            if not reason:
                raise ValueError("Alasan penolakan wajib diisi.")

            self.rejection_reason = reason

        return self


# =========================================================
# CREATE ADMIN EVENT ACCOUNT
# =========================================================

class AdminEventAccountCreate(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=100,
    )

    email: EmailStr

    password: str = Field(
        min_length=8,
        max_length=128,
    )
