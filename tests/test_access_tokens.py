from services.access_token_service import AccessTokenService


def test_access_token_verification():
    service = AccessTokenService()
    token = service.generate_token()

    assert service.verify_token({"access_token": token}, token)
    assert not service.verify_token({"access_token": token}, "wrong")
    assert not service.verify_token({"access_token": token}, None)


def test_missing_legacy_token_allows_access():
    assert AccessTokenService().verify_token({}, None)
