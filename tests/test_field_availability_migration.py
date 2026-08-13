from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models import Base


def test_field_availability_migration_adds_json_column(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'availability.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE form_fields DROP COLUMN availability_json")
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        module_path = Path("migrations/versions/20260813_0035_field_workflow_availability.py")
        namespace = {}
        exec(compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec"), namespace)
        original_op = namespace["op"]
        namespace["op"] = operations
        try:
            namespace["upgrade"]()
        finally:
            namespace["op"] = original_op
    columns = {column["name"] for column in sa.inspect(engine).get_columns("form_fields")}
    assert "availability_json" in columns
