from pathlib import Path

from flask import Flask, url_for


def test_admin_blueprint_imports_from_package():
    import routes.admin as admin_module
    import routes.admin.forms as forms_module
    import routes.admin.logos as logos_module

    module_path = Path(admin_module.__file__)

    assert admin_module.bp.name == "admin"
    assert forms_module.forms_list.__name__ == "forms_list"
    assert logos_module.logos_list.__name__ == "logos_list"
    assert module_path.name == "__init__.py"
    assert module_path.parent.name == "admin"
    assert not (module_path.parent.parent / "admin.py").exists()


def test_logo_endpoints_are_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.logos_list"] == "/admin/logos"
    assert endpoints["admin.logo_toggle"] == "/admin/logos/<int:logo_id>/toggle"
    assert endpoints["admin.logo_edit"] == "/admin/logos/<int:logo_id>/edit"
    assert endpoints["admin.logo_asset"] == "/admin/logos/<int:logo_id>/asset"
    with app.app_context():
        assert url_for("admin.logos_list") == "http://example.test/admin/logos"
        assert url_for("admin.logo_toggle", logo_id=1) == "http://example.test/admin/logos/1/toggle"
        assert url_for("admin.logo_edit", logo_id=1) == "http://example.test/admin/logos/1/edit"
        assert url_for("admin.logo_asset", logo_id=1) == "http://example.test/admin/logos/1/asset"


def test_form_endpoints_are_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.forms_list"] == "/admin/forms"
    assert endpoints["admin.forms_upload"] == "/admin/forms/upload"
    assert endpoints["admin.form_edit"] == "/admin/forms/<int:form_id>/edit"
    assert endpoints["admin.form_delete"] == "/admin/forms/<int:form_id>/delete"
    assert endpoints["admin.form_toggle"] == "/admin/forms/<int:form_id>/toggle"
    assert endpoints["admin.form_fields"] == "/admin/forms/<int:form_id>/fields"
    with app.app_context():
        assert url_for("admin.forms_list") == "http://example.test/admin/forms"
        assert url_for("admin.forms_upload") == "http://example.test/admin/forms/upload"
        assert url_for("admin.form_edit", form_id=1) == "http://example.test/admin/forms/1/edit"
        assert url_for("admin.form_delete", form_id=1) == "http://example.test/admin/forms/1/delete"
        assert url_for("admin.form_toggle", form_id=1) == "http://example.test/admin/forms/1/toggle"
        assert url_for("admin.form_fields", form_id=1) == "http://example.test/admin/forms/1/fields"
