
"""add ticket classes and payment provider

Revision ID: be258d8e50e7
Revises: 246bee50cb39
Create Date: 2026-10-27 00:55:23.824740
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers used by Alembic.
revision: str = "be258d8e50e7"
down_revision: Union[str, Sequence[str], None] = "246bee50cb39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ticket classes table.
    op.create_table(
        "ticket_classes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("available_tickets", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "available_tickets >= 0",
            name="check_ticket_class_stock",
        ),
        sa.CheckConstraint(
            "price >= 0",
            name="check_ticket_class_price",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_ticket_classes_event_id_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "name",
            name="uq_ticket_class_event_name",
        ),
    )

    op.create_index(
        op.f("ix_ticket_classes_event_id"),
        "ticket_classes",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_ticket_classes_id"),
        "ticket_classes",
        ["id"],
        unique=False,
    )

    # Add ticket class reference to existing orders.
    op.add_column(
        "orders",
        sa.Column("ticket_class_id", sa.Integer(), nullable=True),
    )

    op.create_index(
        op.f("ix_orders_ticket_class_id"),
        "orders",
        ["ticket_class_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        "ticket_classes",
        ["ticket_class_id"],
        ["id"],
    )

    # Add payment provider information.
    op.add_column(
        "payments",
        sa.Column(
            "payment_provider",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.create_index(
        op.f("ix_payments_order_id"),
        "payments",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    # Remove payment provider changes.
    op.drop_index(
        op.f("ix_payments_order_id"),
        table_name="payments",
    )

    op.drop_column("payments", "payment_provider")

    # Remove order foreign key and ticket class reference.
    op.drop_constraint(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        type_="foreignkey",
    )

    op.drop_index(
        op.f("ix_orders_ticket_class_id"),
        table_name="orders",
    )

    op.drop_column("orders", "ticket_class_id")

    # Remove ticket classes table.
    op.drop_index(
        op.f("ix_ticket_classes_id"),
        table_name="ticket_classes",
    )

    op.drop_index(
        op.f("ix_ticket_classes_event_id"),
        table_name="ticket_classes",
    )

    op.drop_table("ticket_classes")
