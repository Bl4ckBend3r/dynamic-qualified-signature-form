import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_soft_delete_migration_preserves_rows_and_blocks_lossy_downgrade(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'soft-delete.db'}")
    metadata = sa.MetaData()
    sa.Table("form_submissions", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    migration = importlib.import_module("migrations.versions.20261102_0049_soft_delete_submissions")

    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO form_submissions (id) VALUES (1)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        connection.execute(sa.text(
            "UPDATE form_submissions SET deleted_at = '2026-11-02', deleted_by_user_id = 7 WHERE id = 1"
        ))
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="archived submissions"):
            migration.downgrade()
        row = connection.execute(sa.text(
            "SELECT deleted_at, deleted_by_user_id FROM form_submissions WHERE id = 1"
        )).one()
        assert row.deleted_at is not None and row.deleted_by_user_id == 7
    engine.dispose()
