import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_item_decision_snapshot_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'item-snapshot.db'}")
    metadata = sa.MetaData()
    sa.Table("repeatable_group_item_decisions", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    migration = importlib.import_module("migrations.versions.20260901_0048_item_decision_participant_snapshot")

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    assert "participant_snapshot_json" in {
        column["name"] for column in sa.inspect(engine).get_columns("repeatable_group_item_decisions")
    }

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
    assert "participant_snapshot_json" not in {
        column["name"] for column in sa.inspect(engine).get_columns("repeatable_group_item_decisions")
    }
    engine.dispose()
