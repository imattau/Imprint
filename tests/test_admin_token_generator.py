from app.admin.token import generate_admin_token


def test_generate_admin_token_has_entropy() -> None:
    token = generate_admin_token()
    assert isinstance(token, str)
    # token_urlsafe(32) yields ~43 chars; ensure we didn't accidentally return empty/short string
    assert len(token) >= 30
