from __future__ import annotations

from datetime import datetime
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from models import Base


MIGRATION_MODULE = "migrations.versions.20260824_0045_repair_training_action_history"
TABLE_NAME = "training_participant_action_history"


def _old_schema(metadata: sa.MetaData) -> dict[str, sa.Table]:
    forms = sa.Table(
        "forms",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
    )
    form_versions = sa.Table(
        "form_versions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
    )
    submissions = sa.Table(
        "form_submissions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("form_slug", sa.String(255), nullable=False),
        sa.Column("form_version_id", sa.Integer(), sa.ForeignKey("form_versions.id", ondelete="SET NULL"), nullable=True),
    )
    submission_trainings = sa.Table(
        "submission_trainings",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("training_id", sa.String(255), nullable=False),
    )
    users = sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    history = sa.Table(
        TABLE_NAME,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_training_id",
            sa.Integer(),
            sa.ForeignKey(
                "submission_trainings.id",
                name="fk_old_training_history_submission_training",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.String(128), nullable=False),
        sa.Column("new_value", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    sa.Index(
        "ix_training_participant_history_training",
        history.c.submission_training_id,
        history.c.created_at,
    )
    return {
        "forms": forms,
        "form_versions": form_versions,
        "form_submissions": submissions,
        "submission_trainings": submission_trainings,
        "users": users,
        "history": history,
    }


def _run_upgrade(connection, monkeypatch) -> None:
    migration = importlib.import_module(MIGRATION_MODULE)
    operations = Operations(MigrationContext.configure(connection))
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()


def test_repair_migration_backfills_history_and_changes_fk_without_data_loss(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    tables = _old_schema(metadata)

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata.create_all(connection)
        connection.execute(tables["forms"].insert(), [
            {"id": 10, "slug": "current-form"},
            {"id": 20, "slug": "historical-form"},
        ])
        connection.execute(tables["form_versions"].insert(), {"id": 200, "form_id": 20})
        connection.execute(
            tables["form_submissions"].insert(),
            {"id": 300, "form_slug": "current-form", "form_version_id": 200},
        )
        connection.execute(
            tables["submission_trainings"].insert(),
            {"id": 400, "submission_id": 300, "training_id": "trn_stable_123"},
        )
        connection.execute(
            tables["history"].insert(),
            {
                "id": 500,
                "submission_training_id": 400,
                "action": "cancelled",
                "previous_value": "selected",
                "new_value": "cancelled",
                "reason": "test",
                "metadata_json": {},
                "actor_user_id": None,
                "created_at": datetime(2026, 8, 24, 12, 0, 0),
            },
        )

        _run_upgrade(connection, monkeypatch)
        _run_upgrade(connection, monkeypatch)

        repaired = sa.Table(TABLE_NAME, sa.MetaData(), autoload_with=connection)
        row = connection.execute(sa.select(repaired)).mappings().one()
        assert row["id"] == 500
        assert row["form_id"] == 20
        assert row["training_id"] == "trn_stable_123"
        assert row["submission_training_id"] == 400

        inspector = sa.inspect(connection)
        columns = {item["name"]: item for item in inspector.get_columns(TABLE_NAME)}
        assert columns["form_id"]["nullable"] is False
        assert columns["training_id"]["nullable"] is False
        assert columns["submission_training_id"]["nullable"] is True

        submission_fk = next(
            item
            for item in inspector.get_foreign_keys(TABLE_NAME)
            if item["constrained_columns"] == ["submission_training_id"]
        )
        assert submission_fk["referred_table"] == "submission_trainings"
        assert (submission_fk.get("options") or {}).get("ondelete") == "SET NULL"

        form_fk = next(
            item
            for item in inspector.get_foreign_keys(TABLE_NAME)
            if item["constrained_columns"] == ["form_id"]
        )
        assert form_fk["referred_table"] == "forms"
        assert (form_fk.get("options") or {}).get("ondelete") == "CASCADE"

        scope_index = next(
            item for item in inspector.get_indexes(TABLE_NAME)
            if item["name"] == "ix_training_action_history_scope"
        )
        assert scope_index["column_names"] == ["form_id", "training_id", "created_at"]

        connection.execute(tables["submission_trainings"].delete().where(tables["submission_trainings"].c.id == 400))
        retained = connection.execute(sa.select(repaired)).mappings().one()
        assert retained["id"] == 500
        assert retained["submission_training_id"] is None


def test_repair_migration_refuses_unresolvable_history_without_deleting_it(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    tables = _old_schema(metadata)
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            tables["history"].insert(),
            {
                "id": 1,
                "submission_training_id": 999,
                "action": "legacy",
                "previous_value": "",
                "new_value": "",
                "reason": "",
                "metadata_json": {},
                "actor_user_id": None,
                "created_at": datetime(2026, 8, 24, 12, 0, 0),
            },
        )
        with pytest.raises(RuntimeError, match="nie może jednoznacznie uzupełnić"):
            _run_upgrade(connection, monkeypatch)
        count = connection.scalar(sa.select(sa.func.count()).select_from(sa.table(TABLE_NAME)))
        assert count == 1


def test_target_history_ddl_compiles_for_mariadb_and_postgresql():
    table = Base.metadata.tables[TABLE_NAME]
    scope_index = next(index for index in table.indexes if index.name == "ix_training_action_history_scope")
    for dialect in (mysql.dialect(), postgresql.dialect()):
        table_ddl = str(CreateTable(table).compile(dialect=dialect)).upper()
        index_ddl = str(CreateIndex(scope_index).compile(dialect=dialect)).upper()
        assert "FORM_ID INTEGER NOT NULL" in table_ddl
        assert "TRAINING_ID VARCHAR(255) NOT NULL" in table_ddl
        assert "FOREIGN KEY(SUBMISSION_TRAINING_ID)" in table_ddl
        assert "ON DELETE SET NULL" in table_ddl
        assert "FOREIGN KEY(FORM_ID)" in table_ddl
        assert "ON DELETE CASCADE" in table_ddl
        assert "(FORM_ID, TRAINING_ID, CREATED_AT)" in index_ddl
