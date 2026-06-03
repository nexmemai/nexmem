"""Add users.tier column (missing migration for schema drift fix).

Revision ID: 025_users_tier
Revises: 024_app_suspension
Create Date: 2026-06-04

The ``tier`` column was added to the ``User`` SQLAlchemy model
(``app/models/user.py``) during the Phase 2 quota-enforcement work
but no corresponding Alembic migration was created. This caused
``column users.tier does not exist`` in the CI integration-tests job
because the fresh Postgres database is built only from the Alembic chain
(not from a Supabase snapshot) and therefore never received the column.

Values: 'free' | 'starter' | 'pro' | 'enterprise'.
All existing rows receive 'free' (the server default), which matches
the model default and the quota logic in app/core/quotas.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "025_users_tier"
down_revision: Union[str, None] = "024_app_suspension"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN IF NOT EXISTS so this is safe to run against a
    # Supabase-hosted database where the column may already exist
    # (added via a previous manual ALTER TABLE or a Supabase migration).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'tier'
            ) THEN
                ALTER TABLE users
                    ADD COLUMN tier VARCHAR NOT NULL DEFAULT 'free';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_column("users", "tier")  # lint: raw-alter-ok
