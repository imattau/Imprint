

def test_session_secret_respects_env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "super-secret-value")
    from importlib import reload
    import app.config as cfg

    reload(cfg)
    assert cfg.settings.session_secret == "super-secret-value"
