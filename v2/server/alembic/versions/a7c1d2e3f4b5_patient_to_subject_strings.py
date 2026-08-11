"""patient -> subject job_type and artifact kind strings

Revision ID: a7c1d2e3f4b5
Revises: 64f321c7c569
Create Date: 2026-08-11

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c1d2e3f4b5'
down_revision: str | Sequence[str] | None = '64f321c7c569'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMES = [
    ("jobs", "job_type", "export_patient", "export_subject"),
    ("jobs", "job_type", "import_patient", "import_subject"),
    ("artifacts", "kind", "patient_export", "subject_export"),
]


def upgrade() -> None:
    for table, col, old, new in _RENAMES:
        op.execute(f"UPDATE {table} SET {col} = '{new}' WHERE {col} = '{old}'")


def downgrade() -> None:
    for table, col, old, new in _RENAMES:
        op.execute(f"UPDATE {table} SET {col} = '{old}' WHERE {col} = '{new}'")
