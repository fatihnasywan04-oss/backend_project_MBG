"""add ticket classes and payment provider

Revision ID: 246bee50cb39
Revises: 1d30e9a9914a
Create Date: 2026-10-27 00:53:01.126269

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '246bee50cb39'
down_revision: Union[str, Sequence[str], None] = '1d30e9a9914a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
