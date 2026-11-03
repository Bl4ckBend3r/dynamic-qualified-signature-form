import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("old_soft_delete", [False, True])
def test_soft_delete_migration_preserves_rows_and_blocks_lossy_downgrade(tmp_path, monkeypatch, old_soft_delete):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'soft-delete.db'}")
    metadata = sa.MetaData()
    sa.Table("form_versions", metadata, sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("definition_json", sa.JSON))
    sa.Table("form_submissions", metadata, sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("form_version_id", sa.Integer), sa.Column("data_json", sa.JSON))
    metadata.create_all(engine)
    migration = importlib.import_module("migrations.versions.20261103_0050_soft_delete_submissions")

    with engine.begin() as connection:
        if old_soft_delete:
            connection.execute(sa.text("ALTER TABLE form_submissions ADD COLUMN deleted_at DATETIME"))
            connection.execute(sa.text("ALTER TABLE form_submissions ADD COLUMN deleted_by_user_id INTEGER"))
            connection.execute(sa.text("CREATE INDEX ix_form_submissions_deleted_at ON form_submissions(deleted_at)"))
        connection.execute(metadata.tables["form_versions"].insert().values(id=1, definition_json={"fields": [
            {"name": "choice", "type": "checkbox", "options": ["A", "B"]}]}))
        connection.execute(metadata.tables["form_submissions"].insert().values(id=1, form_version_id=1,
            data_json={"choice": "A", "untouched": "keep"}))
        if old_soft_delete:
            connection.execute(sa.text("UPDATE form_submissions SET deleted_at='2026-11-01', deleted_by_user_id=9 WHERE id=1"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        payload = connection.execute(sa.select(metadata.tables["form_submissions"].c.data_json)).scalar_one()
        assert payload == {"choice": ["A"], "untouched": "keep"}
        if old_soft_delete:
            assert connection.execute(sa.text("SELECT deleted_by_user_id FROM form_submissions WHERE id=1")).scalar_one() == 9
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
