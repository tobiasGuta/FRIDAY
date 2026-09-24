"""SDK-neutral, temporary display of Google Search grounding in Live replies.

This module does not fetch source URLs, keep a search history, or execute HTML.
Search Suggestions are vendor-supplied markup shown in a sandboxed browser frame.
"""

from __future__ import annotations

import html
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from friday.core.events import EventKind, SearchSource, VoiceEvent


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _source_url(value: Any) -> str | None:
    """Display a provider URL, never fetch it; reject non-web and control URLs."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return value


def _title(value: Any) -> str:
    if not isinstance(value, str):
        return "Source"
    safe = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return safe.strip()[:200] or "Source"


def extract_search_grounding(content: Any) -> VoiceEvent | None:
    """Use only metadata returned by Google; never invent URLs or citations."""
    metadata = _field(content, "grounding_metadata")
    if metadata is None:
        return None
    sources: list[SearchSource] = []
    seen: set[str] = set()
    for chunk in _field(metadata, "grounding_chunks") or ():
        web = _field(chunk, "web")
        if web is None:
            continue
        url = _source_url(_field(web, "uri"))
        if url is None or url in seen:
            continue
        seen.add(url)
        sources.append(SearchSource(title=_title(_field(web, "title")), url=url))
        if len(sources) == 10:
            break
    entry = _field(metadata, "search_entry_point")
    snippet = _field(entry, "rendered_content") if entry is not None else None
    if not isinstance(snippet, str) or len(snippet) > 100_000:
        snippet = None
    if not sources and not snippet:
        return None
    return VoiceEvent(
        EventKind.GROUNDING,
        sources=tuple(sources),
        search_suggestions_html=snippet,
    )


class SearchPreview:
    """Create a disposable per-session HTML preview for Google's own widget."""

    def __init__(
        self,
        directory: Path,
        *,
        open_url: Callable[[str], bool] | None = None,
    ) -> None:
        self.directory = directory
        self._open_url = open_url if open_url is not None else webbrowser.open
        self._count = 0

    def show(
        self, answer: str, sources: tuple[SearchSource, ...], search_suggestions_html: str
    ) -> bool:
        if not search_suggestions_html:
            return False
        self._count += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"grounded-turn-{self._count}.html"
        links = "\n".join(
            '<li><a target="_blank" rel="noopener noreferrer" href="'
            + html.escape(item.url, quote=True)
            + '">'
            + html.escape(item.title)
            + "</a></li>"
            for item in sources
        )
        # Escaping the srcdoc ATTRIBUTE leaves Google's markup unmodified when
        # decoded by the browser, while the sandbox blocks injected scripts.
        widget = html.escape(search_suggestions_html, quote=True)
        page = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>FRIDAY — Google Search grounding</title>"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            "<style>body{font:16px system-ui,sans-serif;max-width:54rem;"
            "margin:2rem auto;padding:0 1rem;line-height:1.5}"
            "iframe{border:0;width:100%;min-height:160px}</style></head><body>"
            "<h1>FRIDAY — grounded answer</h1><p>"
            + html.escape(answer)
            + "</p><h2>Sources returned by Google</h2><ol>"
            + links
            + "</ol><h2>Google Search suggestions</h2>"
            '<iframe title="Google Search suggestions" sandbox="allow-popups '
            'allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" srcdoc="'
            + widget
            + '"></iframe></body></html>'
        )
        path.write_text(page, encoding="utf-8")
        return bool(self._open_url(path.as_uri()))
