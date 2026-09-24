"""CUNY Brightspace subscription credential and bounded, read-only sync.

Only an explicit desktop action retrieves the feed. Never print/log the URL,
transport exceptions, response body, or raw iCalendar. No browser or SSO automation.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from friday.brightspace_calendar import (
    MAX_FEED_BYTES,
    AcademicSnapshot,
    AcademicStore,
    BrightspaceError,
    parse_calendar,
)

SERVICE = "FRIDAY.Brightspace.Calendar"
ACCOUNT = "CUNY.personal.readonly"
HOST = "brightspace.cuny.edu"


def validate_feed_url(value: str) -> str:
    """Strict CUNY tenant allowlist; no redirects to arbitrary hosts."""
    if not isinstance(value, str):
        raise BrightspaceError("Enter a valid CUNY Brightspace subscription address.")
    url = value.strip()
    if not url or len(url) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise BrightspaceError("Enter a valid CUNY Brightspace subscription address.")
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and parts.hostname == HOST
            and parts.port in (None, 443)
            and parts.username is None and parts.password is None
            and not parts.fragment and parts.path.startswith("/")
            and len(parts.path) > 1
        )
    except ValueError as exc:
        raise BrightspaceError("Enter a valid CUNY Brightspace subscription address.") from exc
    if not valid:
        raise BrightspaceError("Use the private HTTPS feed from brightspace.cuny.edu.")
    return url


def _vault(vault=None):
    if vault is not None:
        return vault
    try:
        import keyring

        backend = keyring.get_keyring()
        if getattr(backend, "priority", 0) < 1:
            raise BrightspaceError("Protected system credential storage is unavailable.")
        return keyring
    except BrightspaceError:
        raise
    except Exception as exc:
        raise BrightspaceError("Protected system credential storage is unavailable.") from exc


def save_feed(value: str, *, vault=None) -> None:
    url = validate_feed_url(value)
    try:
        _vault(vault).set_password(SERVICE, ACCOUNT, url)
    except BrightspaceError:
        raise
    except Exception as exc:
        raise BrightspaceError("Unable to save the feed in protected credential storage.") from exc


def load_feed(*, vault=None) -> str:
    try:
        value = _vault(vault).get_password(SERVICE, ACCOUNT)
    except BrightspaceError:
        raise
    except Exception as exc:
        raise BrightspaceError("Unable to read protected calendar credentials.") from exc
    if not value:
        raise BrightspaceError("No Brightspace calendar feed has been saved.")
    return validate_feed_url(value)


def forget_feed(*, vault=None, store: AcademicStore | None = None) -> None:
    try:
        _vault(vault).delete_password(SERVICE, ACCOUNT)
    except Exception as exc:
        # If deletion fails, do not claim the credential has been disconnected.
        raise BrightspaceError("Unable to remove the saved Brightspace credential.") from exc
    cache = (store or AcademicStore()).path
    try:
        cache.unlink(missing_ok=True)
    except OSError as exc:
        raise BrightspaceError(
            "Credential removed, but local cached data could not be erased."
        ) from exc


def fetch_feed(url: str, *, client: httpx.Client | None = None) -> bytes:
    """Stream-decompress with a strict response bound; never follow redirects."""
    validated = validate_feed_url(url)

    def read(transport: httpx.Client) -> bytes:
        try:
            with transport.stream(
                "GET", validated, headers={"Accept": "text/calendar"}, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    raise BrightspaceError("Calendar feed redirected; no redirect was followed.")
                if response.status_code in (401, 403):
                    raise BrightspaceError("Brightspace rejected the saved subscription.")
                if response.status_code != 200:
                    raise BrightspaceError("Brightspace calendar could not be retrieved.")
                try:
                    declared = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared = 0
                if declared > MAX_FEED_BYTES:
                    raise BrightspaceError("Calendar response exceeds the size limit.")
                chunks = []
                size = 0
                for chunk in response.iter_bytes(chunk_size=32768):
                    size += len(chunk)
                    if size > MAX_FEED_BYTES:
                        raise BrightspaceError("Calendar response exceeds the size limit.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except BrightspaceError:
            raise
        except httpx.HTTPError as exc:
            raise BrightspaceError(
                "Calendar network request failed; cached data is unchanged."
            ) from exc

    if client is not None:
        return read(client)
    with httpx.Client(timeout=httpx.Timeout(15.0), trust_env=False) as transport:
        return read(transport)


def sync_feed(*, store: AcademicStore | None = None, vault=None,
              client: httpx.Client | None = None) -> AcademicSnapshot:
    """All-or-nothing: download, parse/validate, then replace the local snapshot."""
    url = load_feed(vault=vault)
    payload = fetch_feed(url, client=client)
    items = parse_calendar(payload)
    database = store or AcademicStore()
    database.replace(items)
    return database.snapshot()
