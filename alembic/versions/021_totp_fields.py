"""TOTP / 2FA fields on users (P3-A6, Block 5).

Revision ID: 021_totp_fields
Revises: 020_engrams_app_id
Create Date: 2026-05-23

Adds two columns to ``users`` so the application can store an RFC 6238
TOTP secret per user and gate ``/auth/login`` on it:

* ``totp_secret VARCHAR(32) NULL`` — base32 shared secret. NULL for
  users who have never started TOTP setup. The column length matches
  ``pyotp.random_base32()`` output (default 32 chars). Never returned
  to the client after the initial ``/auth/totp/setup`` response.

* ``totp_enabled BOOLEAN NOT NULL DEFAULT FALSE`` — flipped to TRUE
  only after a successful ``/auth/totp/verify``. Until then the user
  can complete a normal email/password login. Matters for the
  half-completed-setup case: a row with a secret but ``enabled=false``
  must NOT be challenged for a code at login time, otherwise we lock
  the user out of their own account.

No index is added — TOTP is read alongside the user row by primary
key. No backfill is required: existing users continue to log in
exactly as before until they opt in via ``/auth/totp/setup``.

Downgrade drops both columns. Safe because no other table references
them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "021_totp_fields"
down_revision: Union[str, None] = "020_engrams_app_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_secret", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
