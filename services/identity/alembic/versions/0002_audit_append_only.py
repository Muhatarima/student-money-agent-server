"""audit_events is append-only: block UPDATE, DELETE and TRUNCATE at the DB level

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION audit_events_block_changes() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # FOR EACH STATEMENT also catches DELETE/UPDATE on an empty table
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_changes();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION audit_events_block_changes()")
