"""Small async client for the SOMtoday REST API."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
import hashlib
from html.parser import HTMLParser
import logging
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from aiohttp import ClientResponse, ClientResponseError, ClientSession

from .const import (
    AUTHORIZE_URL,
    AUTH_URL,
    CLIENT_ID,
    REDIRECT_URI,
    SCHOOLS_URLS,
    SCOPE,
)

_LOGGER = logging.getLogger(__name__)


class SomtodayAuthError(Exception):
    """Raised when authentication fails."""


class SomtodayApiError(Exception):
    """Raised for an API error."""


class SomtodayClient:
    """Async SOMtoday API client."""

    def __init__(
        self,
        session: ClientSession,
        *,
        school_uuid: str,
        username: str,
        password: str,
        token_data: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.school_uuid = school_uuid
        self.username = username
        self.password = password
        self.token_data = token_data or {}

    @property
    def access_token(self) -> str | None:
        return self.token_data.get("access_token")

    @property
    def refresh_token(self) -> str | None:
        return self.token_data.get("refresh_token")

    @property
    def api_url(self) -> str | None:
        url = self.token_data.get("somtoday_api_url")
        return url.rstrip("/") if url else None

    async def async_login(self) -> dict[str, Any]:
        """Log in through SOMtoday's native authorization-code/PKCE flow."""
        self.session.cookie_jar.clear()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)

        params = {
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "state": state,
            "response_type": "code",
            "scope": SCOPE,
            "tenant_uuid": self.school_uuid,
            "session": "no_session",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        try:
            async with self.session.get(
                AUTHORIZE_URL, params=params, allow_redirects=False, timeout=30
            ) as response:
                code = await self._async_complete_login_form(response, state)
        except SomtodayAuthError:
            raise
        except Exception as err:
            raise SomtodayAuthError(f"SOMtoday login flow failed: {err}") from err

        data = {
            "grant_type": "authorization_code",
            "session": "no_session",
            "scope": SCOPE,
            "client_id": CLIENT_ID,
            "tenant_uuid": self.school_uuid,
            "code": code,
            "code_verifier": verifier,
        }
        try:
            async with self.session.post(AUTH_URL, data=data, timeout=30) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise SomtodayAuthError(
                        payload.get("error_description")
                        or payload.get("error")
                        or f"HTTP {response.status}"
                    )
        except SomtodayAuthError:
            raise
        except Exception as err:
            raise SomtodayAuthError(str(err)) from err

        if not payload.get("access_token") or not payload.get("somtoday_api_url"):
            raise SomtodayAuthError("SOMtoday returned no usable access token/API URL")

        self.token_data = payload
        return payload

    async def _async_complete_login_form(
        self, response: ClientResponse, expected_state: str
    ) -> str:
        """Follow SOMtoday redirects and submit its Wicket login form."""
        submitted_username = False
        submitted_password = False

        for _ in range(10):
            if 300 <= response.status < 400:
                location = response.headers.get("Location")
                if not location:
                    raise SomtodayAuthError("SOMtoday returned a redirect without Location")

                callback_code = _callback_code(location, expected_state)
                if callback_code:
                    response.release()
                    return callback_code

                next_url = urljoin(str(response.url), location)
                response.release()
                response = await self.session.get(
                    next_url, allow_redirects=False, timeout=30
                )
                continue

            if response.status >= 400:
                text = await response.text()
                raise SomtodayAuthError(
                    f"SOMtoday login page returned HTTP {response.status}: {text[:160]}"
                )

            html = await response.text()
            form = _find_login_form(html)
            if form is None:
                raise SomtodayAuthError(
                    "SOMtoday returned no supported username/password form"
                )

            payload: dict[str, str] = {}
            has_username = False
            has_password = False
            for field in form.fields:
                lowered = field.name.lower()
                if "username" in lowered:
                    payload[field.name] = self.username
                    has_username = True
                elif "password" in lowered:
                    payload[field.name] = self.password
                    has_password = True
                elif field.name == "loginLink":
                    payload[field.name] = "x"
                elif field.input_type == "hidden":
                    payload[field.name] = field.value

            # SOMtoday renders this submit control as a button/link in some
            # versions. It is nevertheless always present in the browser's
            # form payload and tells Apache Wicket which action was invoked.
            payload.setdefault("loginLink", "x")

            if not has_username and not has_password:
                raise SomtodayAuthError("SOMtoday returned an unknown login form")
            if has_password and submitted_password:
                hint = _safe_login_page_hint(html)
                cookie_status = _login_cookie_status(self.session)
                detail = f" SOMtoday message: {hint}." if hint else ""
                raise SomtodayAuthError(
                    "The password form was returned after submitting credentials;"
                    f" this may be rejected credentials or a changed login flow.{detail}"
                    f" Session cookies: {cookie_status}"
                )
            if has_username:
                submitted_username = True
            if has_password and not submitted_username:
                raise SomtodayAuthError("SOMtoday requested a password before a username")
            if has_password:
                submitted_password = True

            action = urljoin(str(response.url), form.action or str(response.url))
            referer = str(response.url)
            response.release()
            response = await self.session.post(
                action,
                data=payload,
                headers={
                    "Origin": "https://inloggen.somtoday.nl",
                    "Referer": referer,
                },
                allow_redirects=False,
                timeout=30,
            )

        response.release()
        raise SomtodayAuthError("SOMtoday login exceeded the redirect limit")

    async def async_refresh(self) -> dict[str, Any]:
        """Refresh an access token."""
        if not self.refresh_token:
            return await self.async_login()

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": CLIENT_ID,
            "scope": SCOPE,
        }
        try:
            async with self.session.post(AUTH_URL, data=data, timeout=30) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    _LOGGER.debug("Refresh failed, trying full login: %s", payload)
                    return await self.async_login()
        except Exception:
            return await self.async_login()

        # Some OAuth servers omit unchanged values from a refresh response.
        # Keep those values so a successful refresh cannot break later calls.
        self.token_data = {**self.token_data, **payload}
        return payload

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Perform an authenticated API request."""
        if not self.access_token or not self.api_url:
            await self.async_login()

        request_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        url = f"{self.api_url}{path}"

        async with self.session.request(
            method, url, params=params, headers=request_headers, timeout=30
        ) as response:
            if response.status == 401:
                await self.async_refresh()
                request_headers["Authorization"] = f"Bearer {self.access_token}"
                async with self.session.request(
                    method, url, params=params, headers=request_headers, timeout=30
                ) as retry:
                    if retry.status >= 400:
                        text = await retry.text()
                        raise SomtodayApiError(
                            f"{method} {path} returned HTTP {retry.status}: {text[:300]}"
                        )
                    return await retry.json(content_type=None)

            if response.status >= 400:
                text = await response.text()
                raise SomtodayApiError(
                    f"{method} {path} returned HTTP {response.status}: {text[:300]}"
                )
            return await response.json(content_type=None)

    async def async_students(self) -> list[dict[str, Any]]:
        payload = await self.async_request("GET", "/rest/v1/leerlingen")
        return payload.get("items", [])

    async def async_schedule(
        self, begin: date, end: date
    ) -> list[dict[str, Any]]:
        params = [
            ("sort", "asc-id"),
            ("additional", "vak"),
            ("additional", "docentAfkortingen"),
            ("additional", "leerlingen"),
            ("begindatum", begin.isoformat()),
            ("einddatum", end.isoformat()),
        ]
        payload = await self.async_request("GET", "/rest/v1/afspraken", params=params)
        return payload.get("items", [])

    async def async_grades(self, student_id: int) -> list[dict[str, Any]]:
        """Fetch up to the first 100 current result records."""
        params = [
            ("additional", "berekendRapportCijfer"),
            ("additional", "toetssoortnaam"),
        ]
        payload = await self.async_request(
            "GET",
            f"/rest/v1/resultaten/huidigVoorLeerling/{student_id}",
            params=params,
            headers={"Range": "items=0-99"},
        )
        return payload.get("items", [])

    async def async_homework_appointments(
        self, student_id: int, begin: date
    ) -> list[dict[str, Any]]:
        return await self._async_homework(
            "/rest/v1/studiewijzeritemafspraaktoekenningen", student_id, begin
        )

    async def async_homework_days(
        self, student_id: int, begin: date
    ) -> list[dict[str, Any]]:
        """Fetch homework assigned to a specific day."""
        return await self._async_homework(
            "/rest/v1/studiewijzeritemdagtoekenningen", student_id, begin
        )

    async def async_homework_weeks(
        self, student_id: int, begin: date
    ) -> list[dict[str, Any]]:
        """Fetch homework assigned to a week."""
        return await self._async_homework(
            "/rest/v1/studiewijzeritemweektoekenningen", student_id, begin
        )

    async def _async_homework(
        self, path: str, student_id: int, begin: date
    ) -> list[dict[str, Any]]:
        params = [
            ("begintNaOfOp", begin.isoformat()),
            ("geenDifferentiatieOfGedifferentieerdVoorLeerling", str(student_id)),
            ("additional", "swigemaaktVinkjes"),
            ("additional", "huiswerkgemaakt"),
            ("additional", "lesgroep"),
            ("additional", "leerlingen"),
        ]
        payload = await self.async_request("GET", path, params=params)
        return payload.get("items", [])


async def async_get_schools(session: ClientSession) -> list[dict[str, Any]]:
    """Fetch the archived organisation list, trying all known mirrors."""
    last_error: Exception | None = None
    for url in SCHOOLS_URLS:
        try:
            async with session.get(url, timeout=30) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
            schools = _parse_schools(payload)
            if schools:
                return schools
            last_error = SomtodayApiError("The organisation list was empty")
        except Exception as err:  # A second source/manual entry remains available.
            last_error = err
            _LOGGER.debug("Could not load SOMtoday schools from %s: %s", url, err)

    raise SomtodayApiError("Could not load the archived school list") from last_error


def _parse_schools(payload: Any) -> list[dict[str, Any]]:
    """Normalize the historical organisation-list response shapes."""

    # Documentation has historically shown an outer object/list structure.
    if isinstance(payload, dict):
        candidates = payload.get("instellingen", [])
    elif isinstance(payload, list):
        candidates = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("instellingen"), list):
                candidates.extend(item["instellingen"])
            elif isinstance(item, dict) and "uuid" in item:
                candidates.append(item)
    else:
        candidates = []

    return sorted(
        [x for x in candidates if x.get("uuid") and x.get("naam")],
        key=lambda x: (x.get("naam", "").lower(), x.get("plaats", "").lower()),
    )


def link_id(item: dict[str, Any]) -> int | None:
    """Extract the first link id."""
    links = item.get("links") or []
    if links and isinstance(links[0], dict):
        return links[0].get("id")
    return None


class _FormField:
    """A parsed HTML input field."""

    def __init__(self, name: str, input_type: str, value: str) -> None:
        self.name = name
        self.input_type = input_type
        self.value = value


class _HtmlForm:
    """A parsed HTML form."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.fields: list[_FormField] = []


class _LoginFormParser(HTMLParser):
    """Extract forms without adding a third-party HTML dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_HtmlForm] = []
        self._current: _HtmlForm | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._current = _HtmlForm(values.get("action") or "")
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and values.get("name"):
            self._current.fields.append(
                _FormField(
                    values["name"] or "",
                    (values.get("type") or "text").lower(),
                    values.get("value") or "",
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


class _VisibleTextParser(HTMLParser):
    """Collect visible page text while excluding scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._suppressed:
            self._suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self._suppressed and data.strip():
            self.parts.append(data.strip())


def _find_login_form(html: str) -> _HtmlForm | None:
    parser = _LoginFormParser()
    parser.feed(html)
    for form in parser.forms:
        names = [field.name.lower() for field in form.fields]
        if any("username" in name or "password" in name for name in names):
            return form
    return None


def _callback_code(location: str, expected_state: str) -> str | None:
    """Return and validate a code from the native callback."""
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    if "error" in query:
        raise SomtodayAuthError(query.get("error_description", query["error"])[0])
    if "code" not in query:
        return None
    returned_state = query.get("state", [None])[0]
    if returned_state is not None and returned_state != expected_state:
        raise SomtodayAuthError("SOMtoday returned an invalid OAuth state")
    return query["code"][0]


def _safe_login_page_hint(html: str) -> str | None:
    """Extract only a likely human-readable login error from page text."""
    parser = _VisibleTextParser()
    parser.feed(html)
    text = " ".join(parser.parts)
    sentences = re.split(r"(?<=[.!?])\s+|\s{2,}", text)
    markers = (
        "inloggen mislukt",
        "account geblokkeerd",
        "te vaak",
        "incorrect",
        "onjuist",
        "ongeldig",
        "niet correct",
        "niet bekend",
    )
    for sentence in sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in markers):
            return sentence[:240]
    return None


def _login_cookie_status(session: ClientSession) -> str:
    """Report expected cookie names as booleans, never their secret values."""
    names = {cookie.key.lower() for cookie in session.cookie_jar}
    jsession = "yes" if "jsessionid" in names else "no"
    stickiness = "yes" if any("stickiness" in name for name in names) else "no"
    return f"JSESSIONID={jsession}, stickiness={stickiness}"
