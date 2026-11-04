import pytest

from routes.participant_access import participant_credential, resolve_participant_submission_access
from services.access_token_service import AccessTokenService


def test_access_token_verification():
    service = AccessTokenService()
    token = service.generate_token()

    assert service.verify_required_token({"access_token": token}, token)
    assert not service.verify_required_token({"access_token": token}, "wrong")
    assert not service.verify_required_token({"access_token": token}, None)
    assert not hasattr(service, "verify_token")


@pytest.mark.parametrize("expected,provided", [(None, None), ("", ""), ("token", ""), ("", "token")])
def test_missing_or_empty_token_denies_access(expected, provided):
    assert not AccessTokenService().verify_required_token(
        {"access_token": expected}, provided
    )


@pytest.mark.parametrize(
    "path,data,headers",
    [
        ("/", {}, {"Authorization": "Bearer shared-token"}),
        ("/", {"access_token": "shared-token"}, {}),
        ("/", {"token": "shared-token"}, {}),
        ("/?access_token=shared-token", {}, {}),
        ("/?token=shared-token", {}, {}),
    ],
)
def test_participant_credential_supported_sources(app, path, data, headers):
    with app.test_request_context(path, method="POST", data=data, headers=headers):
        assert participant_credential() == "shared-token"


def test_participant_credential_accepts_repeated_identical_values(app):
    with app.test_request_context(
        "/?token=shared-token",
        method="POST",
        data={"access_token": "shared-token"},
        headers={"Authorization": "Bearer shared-token"},
    ):
        assert participant_credential() == "shared-token"


def test_participant_credential_rejects_conflicting_sources(app):
    with app.test_request_context(
        "/?token=query-token",
        method="POST",
        data={"access_token": "form-token"},
        headers={"Authorization": "Bearer header-token"},
    ):
        assert participant_credential() is None


@pytest.mark.parametrize(
    "expected,provided",
    [(None, "provided-token"), ("", ""), ("", "provided-token")],
)
def test_participant_access_denies_empty_expected_token(app, expected, provided):
    path = f"/?token={provided}" if provided else "/"
    with app.test_request_context(path):
        access = resolve_participant_submission_access(
            "submission-1",
            slug="sample_form",
            submission={
                "submission_id": "submission-1",
                "form_slug": "sample_form",
                "access_token": expected,
            },
        )

    assert access is None
