from pathlib import Path

from flask import Flask, url_for


def test_admin_blueprint_imports_from_package():
    import routes.admin as admin_module
    import routes.admin.auth as auth_module
    import routes.admin.dashboard as dashboard_module
    import routes.admin.forms as forms_module
    import routes.admin.logos as logos_module
    import routes.admin.submissions as submissions_module
    import routes.admin.users as users_module

    module_path = Path(admin_module.__file__)

    assert admin_module.bp.name == "admin"
    assert auth_module.login_required.__name__ == "login_required"
    assert admin_module.login_required is auth_module.login_required
    assert dashboard_module.dashboard.__name__ == "dashboard"
    assert forms_module.forms_list.__name__ == "forms_list"
    assert logos_module.logos_list.__name__ == "logos_list"
    assert submissions_module.submissions_all.__name__ == "submissions_all"
    assert users_module.users_list.__name__ == "users_list"
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


def test_auth_endpoints_are_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.admin_index"] == "/admin/"
    assert endpoints["admin.login"] == "/admin/"
    assert endpoints["admin.logout"] == "/admin/logout"
    with app.app_context():
        assert url_for("admin.admin_index") == "http://example.test/admin/"
        assert url_for("admin.login") == "http://example.test/admin/"
        assert url_for("admin.logout") == "http://example.test/admin/logout"


def test_dashboard_endpoint_is_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.dashboard"] == "/admin/dashboard"
    with app.app_context():
        assert url_for("admin.dashboard") == "http://example.test/admin/dashboard"


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


def test_user_endpoints_are_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.users_list"] == "/admin/users"
    assert endpoints["admin.user_toggle_block"] == "/admin/users/<int:user_id>/toggle-block"
    assert "/admin/users/new" in {rule.rule for rule in app.url_map.iter_rules("admin.user_edit")}
    assert "/admin/users/<int:user_id>/edit" in {rule.rule for rule in app.url_map.iter_rules("admin.user_edit")}
    with app.app_context():
        assert url_for("admin.users_list") == "http://example.test/admin/users"
        assert url_for("admin.user_toggle_block", user_id=1) == "http://example.test/admin/users/1/toggle-block"
        assert url_for("admin.user_edit") == "http://example.test/admin/users/new"
        assert url_for("admin.user_edit", user_id=1) == "http://example.test/admin/users/1/edit"


def test_submission_endpoints_are_registered_on_admin_blueprint():
    import routes.admin as admin_module

    app = Flask(__name__)
    app.config["SERVER_NAME"] = "example.test"
    app.register_blueprint(admin_module.bp)

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}

    assert endpoints["admin.submissions_all"] == "/admin/submissions"
    assert endpoints["admin.submissions_list"] == "/admin/forms/<int:form_id>/submissions"
    assert endpoints["admin.submission_detail"] == "/admin/forms/<int:form_id>/submissions/<int:submission_pk>"
    assert endpoints["admin.submission_decision_update"] == "/admin/forms/<int:form_id>/submissions/<int:submission_pk>/decision"
    with app.app_context():
        assert url_for("admin.submissions_all") == "http://example.test/admin/submissions"
        assert url_for("admin.submissions_list", form_id=1) == "http://example.test/admin/forms/1/submissions"
        assert url_for("admin.submission_detail", form_id=1, submission_pk=2) == "http://example.test/admin/forms/1/submissions/2"
        assert (
            url_for("admin.submission_decision_update", form_id=1, submission_pk=2)
            == "http://example.test/admin/forms/1/submissions/2/decision"
        )
