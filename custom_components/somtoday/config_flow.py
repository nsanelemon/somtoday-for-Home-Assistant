"""Config flow for SOMtoday."""

from __future__ import annotations

import logging
from uuid import UUID

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .api import SomtodayAuthError, SomtodayClient, async_get_schools
from .const import CONF_SCHOOL_NAME, CONF_SCHOOL_UUID, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SomtodayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SOMtoday."""

    VERSION = 1

    def __init__(self) -> None:
        self._schools: dict[str, str] = {}
        self._school_list_loaded = False

    async def async_step_user(self, user_input=None):
        """Select school."""
        if not self._school_list_loaded:
            try:
                schools = await async_get_schools(async_get_clientsession(self.hass))
                self._schools = {
                    school["uuid"]: _school_label(school)
                    for school in schools
                }
            except Exception:
                _LOGGER.exception("Could not load SOMtoday schools")
            self._school_list_loaded = True

        if not self._schools:
            return await self.async_step_manual_school()

        school_options = {
            **self._schools,
            "__manual__": "Mijn school staat er niet bij (UUID handmatig invoeren)",
        }

        if user_input is not None:
            if user_input[CONF_SCHOOL_UUID] == "__manual__":
                return await self.async_step_manual_school()
            self.context["school_uuid"] = user_input[CONF_SCHOOL_UUID]
            self.context["school_name"] = self._schools.get(
                user_input[CONF_SCHOOL_UUID], "SOMtoday"
            )
            return await self.async_step_credentials()

        schema = vol.Schema(
            {
                vol.Required(CONF_SCHOOL_UUID): vol.In(
                    school_options
                )
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema
        )

    async def async_step_manual_school(self, user_input=None):
        """Allow setup when the archived school list is unavailable or stale."""
        errors = {}

        if user_input is not None:
            school_uuid = user_input[CONF_SCHOOL_UUID].strip()
            try:
                UUID(school_uuid)
            except (ValueError, AttributeError):
                errors[CONF_SCHOOL_UUID] = "invalid_uuid"
            else:
                self.context["school_uuid"] = str(UUID(school_uuid))
                self.context["school_name"] = (
                    user_input.get(CONF_SCHOOL_NAME, "").strip() or "SOMtoday"
                )
                return await self.async_step_credentials()

        schema = vol.Schema(
            {
                vol.Required(CONF_SCHOOL_UUID): str,
                vol.Optional(CONF_SCHOOL_NAME): str,
            }
        )
        return self.async_show_form(
            step_id="manual_school", data_schema=schema, errors=errors
        )

    async def async_step_credentials(self, user_input=None):
        """Enter credentials and validate them."""
        errors = {}

        if user_input is not None:
            session = async_create_clientsession(self.hass, auto_cleanup=False)
            client = SomtodayClient(
                session,
                school_uuid=self.context["school_uuid"],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
                students = await client.async_students()
            except SomtodayAuthError as err:
                _LOGGER.warning("SOMtoday authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("SOMtoday validation failed")
                errors["base"] = "cannot_connect"
            else:
                student = students[0] if students else {}
                display_name = " ".join(
                    p
                    for p in [
                        student.get("roepnaam"),
                        student.get("voorvoegsel"),
                        student.get("achternaam"),
                    ]
                    if p
                ).strip()
                unique = f'{self.context["school_uuid"]}:{user_input[CONF_USERNAME]}'
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=display_name or self.context["school_name"],
                    data={
                        CONF_SCHOOL_UUID: self.context["school_uuid"],
                        CONF_SCHOOL_NAME: self.context["school_name"],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            finally:
                session.detach()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"school": self.context.get("school_name", "")},
        )


def _school_label(school: dict) -> str:
    """Build a readable label without an empty pair of parentheses."""
    name = school["naam"]
    place = school.get("plaats", "").strip()
    return f"{name} ({place})" if place else name
