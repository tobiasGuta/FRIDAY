from friday.config import Settings


def test_defaults_without_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = Settings(_env_file=None)
    assert config.provider == "fake"
    assert config.input_sample_rate == 16000
    assert config.output_sample_rate == 24000


def test_key_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-do-not-display")
    config = Settings(_env_file=None)
    assert config.require_gemini_key() == "test-secret-do-not-display"
    assert "test-secret-do-not-display" not in repr(config)


def test_gemini_requires_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = Settings(_env_file=None)
    try:
        config.require_gemini_key()
    except ValueError as exc:
        assert "GEMINI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing-key validation")
