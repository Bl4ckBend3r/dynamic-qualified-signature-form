"""extend training surveys with PRE and POST tests

Revision ID: 20260831_0047
Revises: 20260826_0046
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260831_0047"
down_revision = "20260826_0046"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(JSONB, "postgresql")


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> list[dict]:
    return sa.inspect(op.get_bind()).get_indexes(table_name)


def _unique_constraints(table_name: str) -> list[dict]:
    return sa.inspect(op.get_bind()).get_unique_constraints(table_name)


def _checks(table_name: str) -> list[dict]:
    return sa.inspect(op.get_bind()).get_check_constraints(table_name)


def _has_named_index(table_name: str, name: str) -> bool:
    return any(item.get("name") == name for item in _indexes(table_name))


def _has_unique(table_name: str, name: str, columns: list[str]) -> bool:
    expected = list(columns)
    return any(
        item.get("name") == name or (item.get("unique") and item.get("column_names") == expected)
        for item in [*_unique_constraints(table_name), *_indexes(table_name)]
    )


def _has_plain_index(table_name: str, columns: list[str]) -> bool:
    expected = list(columns)
    return any(
        not item.get("unique") and item.get("column_names") == expected
        for item in _indexes(table_name)
    )


def _check_definition(table_name: str, name: str) -> str:
    for item in _checks(table_name):
        if item.get("name") == name:
            return str(item.get("sqltext") or "")
    return ""


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def _create_check_if_missing(table_name: str, name: str, condition: str) -> None:
    if _check_definition(table_name, name):
        return
    if _dialect_name() == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.create_check_constraint(name, condition)
    else:
        op.create_check_constraint(name, table_name, condition)


def _drop_constraint_if_present(table_name: str, name: str, type_: str) -> None:
    present = (
        bool(_check_definition(table_name, name)) if type_ == "check"
        else any(item.get("name") == name for item in _unique_constraints(table_name))
            or _has_named_index(table_name, name)
    )
    if not present:
        return
    if _dialect_name() == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(name, type_=type_)
    else:
        op.drop_constraint(name, table_name, type_=type_)


def _create_unique_if_missing(table_name: str, name: str, columns: list[str]) -> None:
    if _has_unique(table_name, name, columns):
        return
    if _dialect_name() == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.create_unique_constraint(name, columns)
    else:
        op.create_unique_constraint(name, table_name, columns)


def upgrade() -> None:
    _add_column_if_missing("training_surveys", sa.Column("survey_type", sa.String(16), nullable=False, server_default="survey"))
    _add_column_if_missing("training_surveys", sa.Column("attempt_policy", sa.String(24), nullable=False, server_default="single_attempt"))
    _add_column_if_missing("training_surveys", sa.Column("post_requires_attendance", sa.Boolean(), nullable=False, server_default=sa.false()))
    _create_check_if_missing("training_surveys", "ck_training_surveys_type", "survey_type IN ('survey', 'pre_test', 'post_test')")
    _create_check_if_missing("training_surveys", "ck_training_surveys_attempt_policy", "attempt_policy IN ('single_attempt', 'multiple_attempts')")
    if not _has_named_index("training_surveys", "ix_training_surveys_training_type"):
        op.create_index("ix_training_surveys_training_type", "training_surveys", ["form_id", "training_id", "survey_type", "status"])

    question_check = _check_definition("training_survey_questions", "ck_training_survey_questions_type")
    if question_check and "true_false" not in question_check.lower():
        _drop_constraint_if_present("training_survey_questions", "ck_training_survey_questions_type", "check")
    _add_column_if_missing("training_survey_questions", sa.Column("is_scored", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column_if_missing("training_survey_questions", sa.Column("points", sa.Numeric(10, 2), nullable=False, server_default="0"))
    _add_column_if_missing("training_survey_questions", sa.Column("correct_answers_json", _json_type(), nullable=False, server_default="[]"))
    _add_column_if_missing("training_survey_questions", sa.Column("comparison_key", sa.String(128), nullable=False, server_default=""))
    _create_check_if_missing("training_survey_questions", "ck_training_survey_questions_type", "question_type IN ('scale', 'single_choice', 'multiple_choice', 'true_false', 'text')")

    # MariaDB uses UNIQUE(invitation_id) as the supporting index for this table's
    # foreign key. A separate left-most index must exist before that unique index
    # can be removed. The explicit order is also valid for PostgreSQL and SQLite.
    response_table = "training_survey_responses"
    fk_index_name = "ix_training_survey_responses_invitation_id"
    if not _has_plain_index(response_table, ["invitation_id"]):
        op.create_index(fk_index_name, response_table, ["invitation_id"], unique=False)
    _drop_constraint_if_present(response_table, "uq_training_survey_response_invitation", "unique")
    _add_column_if_missing(response_table, sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
    _add_column_if_missing(response_table, sa.Column("status", sa.String(16), nullable=False, server_default="completed"))
    _add_column_if_missing(response_table, sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    _add_column_if_missing(response_table, sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(response_table, sa.Column("score_points", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing(response_table, sa.Column("max_points", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing(response_table, sa.Column("score_percent", sa.Numeric(7, 2), nullable=True))
    _add_column_if_missing(response_table, sa.Column("test_snapshot_json", _json_type(), nullable=False, server_default="{}"))
    _create_unique_if_missing(response_table, "uq_training_survey_response_attempt", ["invitation_id", "attempt_number"])
    _create_check_if_missing(response_table, "ck_training_survey_responses_status", "status IN ('in_progress', 'completed')")
    op.execute(sa.text("UPDATE training_survey_responses SET completed_at = submitted_at WHERE completed_at IS NULL"))

    _add_column_if_missing("training_survey_answers", sa.Column("question_snapshot_json", _json_type(), nullable=False, server_default="{}"))
    _add_column_if_missing("training_survey_answers", sa.Column("is_correct", sa.Boolean(), nullable=True))
    _add_column_if_missing("training_survey_answers", sa.Column("points_awarded", sa.Numeric(10, 2), nullable=True))
    _add_column_if_missing("training_survey_answers", sa.Column("points_max", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    response_table = "training_survey_responses"
    if "attempt_number" in _columns(response_table):
        duplicate = op.get_bind().execute(sa.text(
            "SELECT invitation_id FROM training_survey_responses "
            "GROUP BY invitation_id HAVING COUNT(*) > 1"
        )).first()
        if duplicate is not None:
            raise RuntimeError(
                "Cannot downgrade training PRE/POST attempts: at least one invitation "
                "has multiple responses. No data was removed."
            )

    for column_name in ("points_max", "points_awarded", "is_correct", "question_snapshot_json"):
        _drop_column_if_present("training_survey_answers", column_name)

    _drop_constraint_if_present(response_table, "ck_training_survey_responses_status", "check")
    _create_unique_if_missing(response_table, "uq_training_survey_response_invitation", ["invitation_id"])
    _drop_constraint_if_present(response_table, "uq_training_survey_response_attempt", "unique")
    if _has_named_index(response_table, "ix_training_survey_responses_invitation_id"):
        op.drop_index("ix_training_survey_responses_invitation_id", table_name=response_table)
    for column_name in ("test_snapshot_json", "score_percent", "max_points", "score_points", "completed_at", "started_at", "status", "attempt_number"):
        _drop_column_if_present(response_table, column_name)

    question_check = _check_definition("training_survey_questions", "ck_training_survey_questions_type")
    if question_check and "true_false" in question_check.lower():
        _drop_constraint_if_present("training_survey_questions", "ck_training_survey_questions_type", "check")
    _create_check_if_missing("training_survey_questions", "ck_training_survey_questions_type", "question_type IN ('scale', 'single_choice', 'multiple_choice', 'text')")
    for column_name in ("comparison_key", "correct_answers_json", "points", "is_scored"):
        _drop_column_if_present("training_survey_questions", column_name)

    if _has_named_index("training_surveys", "ix_training_surveys_training_type"):
        op.drop_index("ix_training_surveys_training_type", table_name="training_surveys")
    _drop_constraint_if_present("training_surveys", "ck_training_surveys_attempt_policy", "check")
    _drop_constraint_if_present("training_surveys", "ck_training_surveys_type", "check")
    for column_name in ("post_requires_attendance", "attempt_policy", "survey_type"):
        _drop_column_if_present("training_surveys", column_name)
