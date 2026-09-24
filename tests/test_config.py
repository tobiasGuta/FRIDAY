import pytest
from pydantic import ValidationError

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


def test_sample_rates_from_env_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FRIDAY_INPUT_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("FRIDAY_OUTPUT_SAMPLE_RATE", raising=False)
    (tmp_path / ".env").write_text(
        "FRIDAY_INPUT_SAMPLE_RATE=16000\nFRIDAY_OUTPUT_SAMPLE_RATE=24000\n"
    )
    config = Settings()
    assert config.input_sample_rate == 16000
    assert config.output_sample_rate == 24000


@pytest.mark.parametrize(
    ("name", "bad_value", "expected_error"),
    [
        ("FRIDAY_INPUT_SAMPLE_RATE", "8000", "must be 16000"),
        ("FRIDAY_OUTPUT_SAMPLE_RATE", "16000", "must be 24000"),
        ("FRIDAY_INPUT_SAMPLE_RATE", "not-a-number", "valid integer"),
    ],
)
def test_reject_unsupported_or_malformed_sample_rates(
    monkeypatch, tmp_path, name, bad_value, expected_error
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{name}={bad_value}\n")
    with pytest.raises(ValidationError, match=expected_error):
        Settings()
