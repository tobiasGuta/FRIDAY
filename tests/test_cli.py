from friday.ui.cli import main


def test_doctor_does_not_print_key(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key")
    assert main(["doctor"]) == 0
    result = capsys.readouterr().out
    assert "Gemini key configured: True" in result
    assert "super-secret-key" not in result


def test_demo_once(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["demo", "--once", "Hello"]) == 0
    output = capsys.readouterr().out
    assert "user: Hello" in output
    assert "assistant: Echo: Hello" in output
