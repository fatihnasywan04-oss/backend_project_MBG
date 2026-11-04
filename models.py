from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Numeric,
    ForeignKey,
    CheckConstraint,
    Boolean,
    JSON,
)
from sqlalchemy.orm import relationship, synonym
from sqlalchemy.sql import func

from database import Base


# =========================================================
# USER
# =========================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    # user, admin_event, admin
    role = Column(
        String(50),
        nullable=False,
        default="user",
        server_default="user",
    )

    # none, pending, approved, rejected
    admin_event_status = Column(
        String(30),
        nullable=False,
        default="none",
        server_default="none",
        index=True,
    )

    admin_event_reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    admin_event_reviewed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    admin_event_rejection_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'admin_event', 'admin')",
            name="check_user_role_valid",
        ),
        CheckConstraint(
            "admin_event_status IN ('none', 'pending', 'approved', 'rejected')",
            name="check_admin_event_status_valid",
        ),
    )

    orders = relationship(
        "Order",
        back_populates="user",
    )

    created_events = relationship(
        "Event",
        back_populates="creator",
        foreign_keys="Event.created_by",
    )

    admin_event_reviewed_by_user = relationship(
        "User",
        remote_side="User.id",
        foreign_keys=[admin_event_reviewed_by],
    )

    event_update_requests = relationship(
        "EventUpdateRequest",
        back_populates="requested_by_user",
        foreign_keys="EventUpdateRequest.requested_by",
    )

    event_deletion_requests = relationship(
        "EventDeletionRequest",
        back_populates="requested_by_user",
        foreign_keys="EventDeletionRequest.requested_by",
    )


# =========================================================
# EVENT
# =========================================================

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(String(255), nullable=False)
    category = Column(String(100), nullable=True)
    instagram_username = Column(String(30), nullable=True)

    event_date = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    end_date = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    price = Column(Numeric(12, 2), nullable=False)
    available_tickets = Column(Integer, nullable=False)
    image_url = Column(String(500), nullable=True)

    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    is_cancelled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # pending, approved, rejected
    approval_status = Column(
        String(30),
        nullable=False,
        default="approved",
        server_default="approved",
        index=True,
    )

    approval_reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    approval_reviewed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    approval_rejection_reason = Column(Text, nullable=True)

    # Soft delete
    is_deleted = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    deleted_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Alias kompatibilitas kode lama
    start_date = synonym("event_date")

    __table_args__ = (
        CheckConstraint(
            "price >= 0",
            name="check_event_price_nonnegative",
        ),
        CheckConstraint(
            "available_tickets >= 0",
            name="check_event_available_tickets_nonnegative",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= event_date",
            name="check_event_end_date_valid",
        ),
        CheckConstraint(
            "approval_status IN ('pending', 'approved', 'rejected')",
            name="check_event_approval_status_valid",
        ),
    )

    creator = relationship(
        "User",
        back_populates="created_events",
        foreign_keys=[created_by],
    )

    approval_reviewer = relationship(
        "User",
        foreign_keys=[approval_reviewed_by],
    )

    deletion_reviewer = relationship(
        "User",
        foreign_keys=[deleted_by],
    )

    ticket_classes = relationship(
        "TicketClass",
        back_populates="event",
        passive_deletes=True,
    )

    orders = relationship(
        "Order",
        back_populates="event",
        passive_deletes=True,
    )

    update_requests = relationship(
        "EventUpdateRequest",
        back_populates="event",
        passive_deletes=True,
    )

    deletion_requests = relationship(
        "EventDeletionRequest",
        back_populates="event",
        passive_deletes=True,
    )


# =========================================================
# TICKET CLASS
# =========================================================

class TicketClass(Base):
    __tablename__ = "ticket_classes"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name = Column(String(100), nullable=False)
    price = Column(Numeric(12, 2), nullable=False)

    # Atribut Python: stock
    # Kolom database: available_tickets
    stock = Column(
        "available_tickets",
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    available_tickets = synonym("stock")

    __table_args__ = (
        CheckConstraint(
            "price >= 0",
            name="check_ticket_class_price_nonnegative",
        ),
        CheckConstraint(
            "available_tickets >= 0",
            name="check_ticket_class_stock_nonnegative",
        ),
    )

    event = relationship(
        "Event",
        back_populates="ticket_classes",
    )

    orders = relationship(
        "Order",
        back_populates="ticket_class",
        passive_deletes=True,
    )


# =========================================================
# EVENT UPDATE REQUEST
# =========================================================

class EventUpdateRequest(Base):
    __tablename__ = "event_update_requests"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    requested_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    proposed_data = Column(
        JSON,
        nullable=False,
    )

    # pending, approved, rejected
    status = Column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejection_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="check_event_update_request_status_valid",
        ),
    )

    event = relationship(
        "Event",
        back_populates="update_requests",
    )

    requested_by_user = relationship(
        "User",
        back_populates="event_update_requests",
        foreign_keys=[requested_by],
    )

    reviewer = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )


# =========================================================
# EVENT DELETION REQUEST
# =========================================================

class EventDeletionRequest(Base):
    __tablename__ = "event_deletion_requests"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    requested_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    reason = Column(Text, nullable=True)

    # pending, approved, rejected
    status = Column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejection_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="check_event_deletion_request_status_valid",
        ),
    )

    event = relationship(
        "Event",
        back_populates="deletion_requests",
    )

    requested_by_user = relationship(
        "User",
        back_populates="event_deletion_requests",
        foreign_keys=[requested_by],
    )

    reviewer = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )


# =========================================================
# ORDER
# =========================================================

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    ticket_class_id = Column(
        Integer,
        ForeignKey("ticket_classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quantity = Column(Integer, nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)

    status = Column(
        String(30),
        nullable=False,
        default="pending",
    )

    stock_reserved = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="check_order_quantity_positive",
        ),
        CheckConstraint(
            "total_price >= 0",
            name="check_order_total_price_nonnegative",
        ),
    )

    user = relationship(
        "User",
        back_populates="orders",
    )

    event = relationship(
        "Event",
        back_populates="orders",
    )

    ticket_class = relationship(
        "TicketClass",
        back_populates="orders",
    )

    payments = relationship(
        "Payment",
        back_populates="order",
        passive_deletes=True,
    )


# =========================================================
# PAYMENT
# =========================================================

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    amount = Column(Numeric(12, 2), nullable=False)

    # Atribut Python: method
    # Kolom database: payment_method
    method = Column(
        "payment_method",
        String(50),
        nullable=True,
    )

    # Atribut Python: status
    # Kolom database: payment_status
    status = Column(
        "payment_status",
        String(30),
        nullable=False,
        default="pending",
    )

    transaction_id = Column(
        String(255),
        nullable=True,
        unique=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    paid_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    payment_provider = Column(
        String(100),
        nullable=True,
    )

    # Alias kompatibilitas kode lama
    payment_method = synonym("method")
    payment_status = synonym("status")

    __table_args__ = (
        CheckConstraint(
            "amount >= 0",
            name="check_payment_amount_nonnegative",
        ),
    )

    order = relationship(
        "Order",
        back_populates="payments",
    )