import importlib

from sqlalchemy import JSON, Column, Integer, MetaData, Table, create_engine, select


def test_mail_logo_layout_migration_adds_json_defaults(monkeypatch):
    migration = importlib.import_module(
        "migrations.versions.20260715_0016_mail_logo_layout"
    )
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    settings = Table(
        "system_mail_settings",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("layout_config", JSON, nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(settings.insert().values(id=1, layout_config={"platform_name": "Brand"}))
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        layout = connection.execute(select(settings.c.layout_config).where(settings.c.id == 1)).scalar_one()

    assert migration.revision == "20260715_0016"
    assert migration.down_revision == "20260715_0015"
    assert layout == {
        "platform_name": "Brand",
        "logo_position": "footer",
        "logo_alignment": "center",
        "logo_height_px": 64,
    }
