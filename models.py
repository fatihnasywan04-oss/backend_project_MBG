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
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)

    # Sesuai dengan nama kolom di database Neon.
    password_hash = Column(String(255), nullable=False)

    # Sesuai dengan kolom role di database Neon.
    role = Column(String(50), nullable=False, default="user")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    orders = relationship(
        "Order",
        back_populates="user",
    )


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    image_url = Column(String(500), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ticket_classes = relationship(
        "TicketClass",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    orders = relationship(
        "Order",
        back_populates="event",
    )


class TicketClass(Base):
    __tablename__ = "ticket_classes"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(100), nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    stock = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "price >= 0",
            name="check_ticket_class_price_nonnegative",
        ),
        CheckConstraint(
            "stock >= 0",
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
    )


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    event_id = Column(
        Integer,
        ForeignKey("events.id"),
        nullable=False,
        index=True,
    )

    ticket_class_id = Column(
        Integer,
        ForeignKey("ticket_classes.id"),
        nullable=False,
        index=True,
    )

    quantity = Column(Integer, nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    status = Column(String(30), nullable=False, default="pending")

    # Menandai apakah stok tiket sudah dikurangi untuk order ini.
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
        cascade="all, delete-orphan",
    )


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(50), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    transaction_id = Column(String(255), nullable=True, unique=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

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