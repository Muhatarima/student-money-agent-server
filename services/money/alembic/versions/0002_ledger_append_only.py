"""ledger_entries is append-only: block UPDATE and DELETE at the DB level

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
        CREATE FUNCTION ledger_entries_block_changes() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_entries is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # FOR EACH STATEMENT also catches DELETE/UPDATE on an empty table
    op.execute(
        """
        CREATE TRIGGER ledger_entries_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger_entries
        FOR EACH STATEMENT EXECUTE FUNCTION ledger_entries_block_changes();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ledger_entries_append_only ON ledger_entries")
    op.execute("DROP FUNCTION ledger_entries_block_changes()")
