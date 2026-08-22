"""One account-verification payment reference may prove ownership for only one creator.

A single micro-deposit receipt must not verify two accounts; the application
layer had a check-then-act race. This unique index is the backstop.

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-08-22
"""
import sqlalchemy as sa

from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def _av_ref_already_unique(bind) -> bool:
    """True when some unique constraint/index already covers the column.

    Databases bootstrapped by ``Base.metadata.create_all`` (see earlier
    revisions) already carry it because the model declares ``unique=True``;
    creating another one would be redundant.
    """
    inspector = sa.inspect(bind)
    if not inspector.has_table("creators"):
        return True  # nothing to protect yet; later migrations create it fresh
    try:
        for uc in inspector.get_unique_constraints("creators"):
            if uc.get("column_names") == ["account_verification_ref"]:
                return True
    except NotImplementedError:  # pragma: no cover - dialect specific
        pass
    for idx in inspector.get_indexes("creators"):
        if idx.get("unique") and idx.get("column_names") == ["account_verification_ref"]:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    if _av_ref_already_unique(bind):
        return

    # Legacy databases created before the model declared unique=True may hold
    # duplicates from the old check-then-act race. Creating the constraint on
    # such data would fail and abort `alembic upgrade head`, which runs in the
    # container CMD — i.e. a deploy-time outage. Keep the newest claim (max id)
    # and null out older duplicates; affected creators can simply re-verify.
    op.execute(
        """
        UPDATE creators
        SET account_verification_ref = NULL
        WHERE account_verification_ref IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM creators newer
              WHERE newer.account_verification_ref
                    = creators.account_verification_ref
                AND newer.id > creators.id
          )
        """
    )
    with op.batch_alter_table("creators") as batch:
        batch.create_unique_constraint(
            "uq_creators_account_verification_ref",
            ["account_verification_ref"],
        )


def downgrade() -> None:
    # Only remove what this revision itself created: databases whose uniqueness
    # comes from bootstrap create_all carry an anonymous index instead.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("creators"):
        return
    names = {uc.get("name") for uc in inspector.get_unique_constraints("creators")}
    if "uq_creators_account_verification_ref" not in names:
        return
    with op.batch_alter_table("creators") as batch:
        batch.drop_constraint(
            "uq_creators_account_verification_ref",
            type_="unique",
        )
