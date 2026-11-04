"""add admin event approval and soft delete

Revision ID: a7c91e2f4b60
Revises: 1db718f43b51
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers used by Alembic.
revision: str = "a7c91e2f4b60"
down_revision: Union[str, Sequence[str], None] = "1db718f43b51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =====================================================
    # USERS: ADMIN EVENT APPLICATION
    # =====================================================

    op.add_column(
        "users",
        sa.Column(
            "admin_event_status",
            sa.String(length=30),
            nullable=False,
            server_default="none",
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "admin_event_reviewed_by",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "admin_event_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "admin_event_rejection_reason",
            sa.Text(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_users_admin_event_status",
        "users",
        ["admin_event_status"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_users_admin_event_reviewed_by",
        "users",
        "users",
        ["admin_event_reviewed_by"],
        ["id"],
    )

    op.create_check_constraint(
        "check_admin_event_status_valid",
        "users",
        "admin_event_status IN ('none', 'pending', 'approved', 'rejected')",
    )

    # =====================================================
    # EVENTS: APPROVAL AND SOFT DELETE
    # =====================================================

    op.add_column(
        "events",
        sa.Column(
            "approval_status",
            sa.String(length=30),
            nullable=False,
            server_default="approved",
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "approval_reviewed_by",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "approval_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "approval_rejection_reason",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "events",
        sa.Column(
            "deleted_by",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_events_approval_status",
        "events",
        ["approval_status"],
        unique=False,
    )

    op.create_index(
        "ix_events_is_deleted",
        "events",
        ["is_deleted"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_events_approval_reviewed_by",
        "events",
        "users",
        ["approval_reviewed_by"],
        ["id"],
    )

    op.create_foreign_key(
        "fk_events_deleted_by",
        "events",
        "users",
        ["deleted_by"],
        ["id"],
    )

    op.create_check_constraint(
        "check_event_approval_status_valid",
        "events",
        "approval_status IN ('pending', 'approved', 'rejected')",
    )

    # =====================================================
    # TICKET CLASSES: ALIGN WITH MODEL
    # =====================================================

    op.alter_column(
        "ticket_classes",
        "name",
        existing_type=sa.String(length=30),
        type_=sa.String(length=100),
        existing_nullable=False,
    )

    # =====================================================
    # ORDERS: REQUIRE TICKET CLASS
    # =====================================================

    op.alter_column(
        "orders",
        "ticket_class_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # Align foreign key with the current model.
    op.drop_constraint(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        "ticket_classes",
        ["ticket_class_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # =====================================================
    # EVENT UPDATE REQUESTS
    # =====================================================

    op.create_table(
        "event_update_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("proposed_data", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="check_event_update_request_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_update_requests_event_id",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_event_update_requests_requested_by",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_event_update_requests_reviewed_by",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_event_update_requests_event_id",
        "event_update_requests",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        "ix_event_update_requests_requested_by",
        "event_update_requests",
        ["requested_by"],
        unique=False,
    )

    op.create_index(
        "ix_event_update_requests_status",
        "event_update_requests",
        ["status"],
        unique=False,
    )

    # =====================================================
    # EVENT DELETION REQUESTS
    # =====================================================

    op.create_table(
        "event_deletion_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="check_event_deletion_request_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_deletion_requests_event_id",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_event_deletion_requests_requested_by",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_event_deletion_requests_reviewed_by",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_event_deletion_requests_event_id",
        "event_deletion_requests",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        "ix_event_deletion_requests_requested_by",
        "event_deletion_requests",
        ["requested_by"],
        unique=False,
    )

    op.create_index(
        "ix_event_deletion_requests_status",
        "event_deletion_requests",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    # =====================================================
    # EVENT DELETION REQUESTS
    # =====================================================

    op.drop_index(
        "ix_event_deletion_requests_status",
        table_name="event_deletion_requests",
    )

    op.drop_index(
        "ix_event_deletion_requests_requested_by",
        table_name="event_deletion_requests",
    )

    op.drop_index(
        "ix_event_deletion_requests_event_id",
        table_name="event_deletion_requests",
    )

    op.drop_table("event_deletion_requests")

    # =====================================================
    # EVENT UPDATE REQUESTS
    # =====================================================

    op.drop_index(
        "ix_event_update_requests_status",
        table_name="event_update_requests",
    )

    op.drop_index(
        "ix_event_update_requests_requested_by",
        table_name="event_update_requests",
    )

    op.drop_index(
        "ix_event_update_requests_event_id",
        table_name="event_update_requests",
    )

    op.drop_table("event_update_requests")

    # =====================================================
    # ORDERS
    # =====================================================

    op.drop_constraint(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_orders_ticket_class_id_ticket_classes",
        "orders",
        "ticket_classes",
        ["ticket_class_id"],
        ["id"],
    )

    op.alter_column(
        "orders",
        "ticket_class_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # =====================================================
    # TICKET CLASSES
    # =====================================================

    op.alter_column(
        "ticket_classes",
        "name",
        existing_type=sa.String(length=100),
        type_=sa.String(length=30),
        existing_nullable=False,
    )

    # =====================================================
    # EVENTS
    # =====================================================

    op.drop_constraint(
        "check_event_approval_status_valid",
        "events",
        type_="check",
    )

    op.drop_constraint(
        "fk_events_deleted_by",
        "events",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_events_approval_reviewed_by",
        "events",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_events_is_deleted",
        table_name="events",
    )

    op.drop_index(
        "ix_events_approval_status",
        table_name="events",
    )

    op.drop_column("events", "deleted_by")
    op.drop_column("events", "deleted_at")
    op.drop_column("events", "is_deleted")
    op.drop_column("events", "approval_rejection_reason")
    op.drop_column("events", "approval_reviewed_at")
    op.drop_column("events", "approval_reviewed_by")
    op.drop_column("events", "approval_status")

    # =====================================================
    # USERS
    # =====================================================

    op.drop_constraint(
        "check_admin_event_status_valid",
        "users",
        type_="check",
    )

    op.drop_constraint(
        "fk_users_admin_event_reviewed_by",
        "users",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_users_admin_event_status",
        table_name="users",
    )

    op.drop_column("users", "admin_event_rejection_reason")
    op.drop_column("users", "admin_event_reviewed_at")
    op.drop_column("users", "admin_event_reviewed_by")
    op.drop_column("users", "admin_event_status")