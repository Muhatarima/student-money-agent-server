"""audit append only trigger

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 12:01:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only PostgreSQL supports PL/pgSQL triggers; in SQLite (used in tests),
    # this check safely prevents failures.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION block_audit_events_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only: UPDATE and DELETE are blocked';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER audit_events_prevent_mutation
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW
            EXECUTE FUNCTION block_audit_events_mutation();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            DROP TRIGGER IF EXISTS audit_events_prevent_mutation ON audit_events;
            DROP FUNCTION IF EXISTS block_audit_events_mutation();
        """)
