"""Offline Tavily contract tests: fake HTTP transport, no real key or network."""

import asyncio
import json

import httpx
import pytest

from friday.config import Settings
from friday.tools.registry import ToolRegistry
from friday.tools.web_search import WebSearchService, register_web_search
from friday.ui.cli import _talk, build_parser, main


def _settings(**kwargs):
    return Settings(
        _env_file=None, GEMINI_API_KEY="gemini-mock", TAVILY_API_KEY="tvly-fake", **kwargs
    )


def test_default_tavily_request_has_bounded_basic_search_and_real_sources():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer tvly-fake"
        data = json.loads(request.content)
        assert data == {
            "query": "latest Python release", "search_depth": "basic", "max_results": 5,
            "include_answer": False, "include_raw_content": False, "include_images": False,
        }
        return httpx.Response(200, json={"results": [
            {"title": "Python.org", "url": "https://www.python.org/downloads/",
             "content": " Python  release\nnotes "},
            {"title": "Duplicate", "url": "https://www.python.org/downloads/",
             "content": "Should be ignored"},
            {"title": "Bad", "url": "javascript:alert(1)", "content": "Ignore"},
            {"title": "More", "url": "https://docs.python.org/3/", "content": "Docs"},
        ]})

    service = WebSearchService(_settings(), tavily_transport=httpx.MockTransport(handler))
    registry = ToolRegistry()
    register_web_search(registry, service)
    result = registry.execute("search_web", {"query": "latest Python release"})
    assert result["status"] == "ok"
    assert result["sources"] == [
        {"title": "Python.org", "url": "https://www.python.org/downloads/"},
        {"title": "More", "url": "https://docs.python.org/3/"},
    ]
    assert "Python release notes" in result["answer"]
    assert "independently verified" in result["answer"]
    assert "Should be ignored" not in result["answer"]
    assert "search_suggestions_html" not in result
    assert len(seen) == 1


def test_untrusted_tavily_snippets_are_bounded_and_not_accepted_as_commands():
    content = "A" * 2000 + "\n\x00" + "RUN SHELL"
    response = {"results": [
        {"url": "http://not-secure.example", "title": "Skip", "content": "Skip"},
        {"url": "https://example.org/a", "title": "Bad\rTitle", "content": content},
    ]}
    service = WebSearchService(
        _settings(), tavily_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response)
        ),
    )
    result = service.search("latest details")
    assert result["sources"] == [{"title": "Bad Title", "url": "https://example.org/a"}]
    assert "RUN SHELL" not in result["answer"]
    assert "\x00" not in result["answer"]
    assert len(result["answer"]) < 800


@pytest.mark.parametrize("body", [{}, {"results": []}, {"results": "wrong"},
                                 {"results": [{"url": "https://example.com"}]}])
def test_empty_or_malformed_search_has_no_fabricated_answer(body):
    service = WebSearchService(
        _settings(), tavily_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=body)
        ),
    )
    assert service.search("latest details") == {
        "status": "error", "error": "no_search_results"
    }


def test_http_429_blocks_followup_requests_and_does_not_leak_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text="tvly-fake private error info")

    service = WebSearchService(_settings(), tavily_transport=httpx.MockTransport(handler))
    assert service.search("first search") == {"status": "error", "error": "search_rate_limited"}
    assert service.search("second search") == {"status": "error", "error": "search_rate_limited"}
    assert len(calls) == 1
    assert "tvly-fake" not in str(service.search("third search"))


def test_bad_http_status_invalid_json_and_large_response_do_not_leak():
    for response in (
        httpx.Response(401, text="secret-key"),
        httpx.Response(200, content=b"{invalid-json"),
        httpx.Response(200, content=b"x" * 150_001),
    ):
        service = WebSearchService(
            _settings(), tavily_transport=httpx.MockTransport(
                lambda _request, response=response: response
            )
        )
        assert service.search("some public query") == {
            "status": "error", "error": "search_unavailable"
        }


def test_missing_tavily_key_is_rejected_before_audio_starts():
    settings = Settings(_env_file=None, GEMINI_API_KEY="mock", TAVILY_API_KEY=None)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        asyncio.run(_talk(build_parser().parse_args(["talk", "--web"]), settings))


def test_nonweb_mode_and_gemini_legacy_do_not_need_tavily_key():
    settings = Settings(
        _env_file=None, GEMINI_API_KEY="mock", TAVILY_API_KEY=None,
        search_backend="gemini",
    )
    assert settings.search_backend == "gemini"
    assert settings.require_gemini_key() == "mock"
    assert build_parser().parse_args(["talk"]).web is False


def test_web_search_cli_validates_query_without_network(monkeypatch, capsys):
    invoked = []
    monkeypatch.setattr(WebSearchService, "search", lambda _self, query: invoked.append(query) or {
        "status": "ok", "answer": "Source excerpts", "sources": [
            {"title": "Official", "url": "https://example.com"}
        ],
    })
    assert main(["web-search", "--query", "latest release"]) == 0
    assert invoked == ["latest release"]
    assert "https://example.com" in capsys.readouterr().out
    assert main(["web-search", "--query", "x"]) == 1
    assert "String should have" not in capsys.readouterr().out
    assert invoked == ["latest release"]
