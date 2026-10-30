import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_configurable_decisions_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'decisions.db'}")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("logos", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("forms", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "form_submissions", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("officer_decision", sa.String(32), nullable=False, server_default=""),
    )
    sa.Table(
        "submission_decisions", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("decision", sa.String(64), nullable=False),
    )
    sa.Table("email_logs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    migration = importlib.import_module("migrations.versions.20260824_0043_configurable_item_decisions")

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

    inspector = sa.inspect(engine)
    assert {"decision_type_definitions", "repeatable_group_item_decisions"} <= set(inspector.get_table_names())
    assert "project_logo_id" in {column["name"] for column in inspector.get_columns("forms")}
    assert {"decision_label", "semantic_category", "workflow_step", "target_step"} <= {
        column["name"] for column in inspector.get_columns("submission_decisions")
    }
    assert {"repeatable_group_key", "repeatable_item_id", "item_decision_id"} <= {
        column["name"] for column in inspector.get_columns("email_logs")
    }

    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
    inspector = sa.inspect(engine)
    assert "decision_type_definitions" not in inspector.get_table_names()
    assert "repeatable_group_item_decisions" not in inspector.get_table_names()
    assert "project_logo_id" not in {column["name"] for column in inspector.get_columns("forms")}
    engine.dispose()
