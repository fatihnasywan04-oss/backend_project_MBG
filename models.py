
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Numeric,
    ForeignKey,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    orders = relationship("Order", back_populates="user")


class Event(Base):
    __tablename__ = "events"

    __table_args__ = (
        CheckConstraint("price >= 0", name="check_event_price"),
        CheckConstraint(
            "available_tickets >= 0",
            name="check_event_tickets",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(String(255), nullable=False)

    event_date = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    price = Column(Numeric(12, 2), nullable=False)

    available_tickets = Column(
        Integer,
        nullable=False,
        default=0,
    )

    image_url = Column(String(500), nullable=True)

    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    orders = relationship("Order", back_populates="event")


class Order(Base):
    __tablename__ = "orders"

    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_order_quantity"),
        CheckConstraint("total_price >= 0", name="check_order_total"),
    )

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    event_id = Column(
        Integer,
        ForeignKey("events.id"),
        nullable=False,
    )

    quantity = Column(Integer, nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), nullable=False, default="pending")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="orders")
    event = relationship("Event", back_populates="orders")
    payments = relationship("Payment", back_populates="order")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)

    order_id = Column(
        Integer,
        ForeignKey("orders.id"),
        nullable=False,
    )

    amount = Column(Numeric(12, 2), nullable=False)
    payment_method = Column(String(50), nullable=True)

    payment_status = Column(
        String(20),
        nullable=False,
        default="pending",
    )

    transaction_id = Column(
        String(255),
        unique=True,
        nullable=True,
    )

    paid_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    order = relationship("Order", back_populates="payments")
