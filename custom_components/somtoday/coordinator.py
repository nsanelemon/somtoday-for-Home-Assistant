"""DataUpdateCoordinator for SOMtoday."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SomtodayApiError, SomtodayAuthError, SomtodayClient, link_id
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SomtodayCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate SOMtoday polling."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: SomtodayClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=10),
            config_entry=entry,
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            now = dt_util.now()
            today = now.date()
            students = await self.client.async_students()

            if not students:
                return {
                    "students": [],
                    "by_student": {},
                    "student": None,
                    "student_id": None,
                    "schedule": [],
                    "grades": [],
                    "homework": [],
                    "errors": {},
                }

            errors: dict[str, str] = {}
            schedule = await self._async_optional(
                "afspraken",
                self.client.async_schedule(today, today + timedelta(days=14)),
                errors,
            )

            by_student: dict[str, dict[str, Any]] = {}
            for student in students:
                student_id = link_id(student)
                if student_id is None:
                    continue
                student_errors: dict[str, str] = {}
                grades = await self._async_optional(
                    "cijfers", self.client.async_grades(student_id), student_errors
                )
                homework: list[dict[str, Any]] = []
                homework_requests = (
                    ("afspraak", self.client.async_homework_appointments),
                    ("dag", self.client.async_homework_days),
                    ("week", self.client.async_homework_weeks),
                )
                for source, method in homework_requests:
                    items = await self._async_optional(
                        f"huiswerk_{source}", method(student_id, today), student_errors
                    )
                    homework.extend({**item, "_somtoday_source": source} for item in items)

                scoped_homework = _homework_for_student(homework, student_id)

                by_student[str(student_id)] = {
                    "student": student,
                    "schedule": _schedule_for_student(schedule, student_id),
                    "grades": grades,
                    "homework": _deduplicate_homework(scoped_homework),
                    "homework_received": len(homework),
                    "homework_after_student_filter": len(scoped_homework),
                    "errors": student_errors,
                }

            first_id = link_id(students[0])
            first = by_student.get(str(first_id), {})

            return {
                "students": students,
                "by_student": by_student,
                # Keep the old keys for backwards compatibility and diagnostics.
                "student": students[0],
                "student_id": first_id,
                "schedule": first.get("schedule", schedule),
                "grades": first.get("grades", []),
                "homework": first.get("homework", []),
                "errors": errors,
            }
        except (SomtodayApiError, SomtodayAuthError) as err:
            raise UpdateFailed(str(err)) from err

    async def _async_optional(
        self,
        name: str,
        request,
        errors: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Fetch one optional data group without blocking all entities."""
        try:
            return await request
        except SomtodayApiError as err:
            message = str(err)
            errors[name] = message
            _LOGGER.warning("Could not update SOMtoday %s: %s", name, message)
            return []


def _schedule_for_student(
    schedule: list[dict[str, Any]], student_id: int
) -> list[dict[str, Any]]:
    """Return appointments for a student, retaining unscoped appointments."""
    result = []
    for item in schedule:
        wrapper = (item.get("additionalObjects") or {}).get("leerlingen") or {}
        students = wrapper.get("items") if isinstance(wrapper, dict) else wrapper
        ids = {
            link_id(student)
            for student in (students or [])
            if isinstance(student, dict)
        }
        ids.discard(None)
        if not ids or student_id in ids:
            result.append(item)
    return result


def _deduplicate_homework(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate homework returned through overlapping assignment routes."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        swi = item.get("studiewijzerItem") or {}
        key = (
            link_id(item),
            item.get("datumTijd") or item.get("beginDatumTijd") or item.get("datum"),
            swi.get("onderwerp"),
            swi.get("omschrijving"),
        )
        unique.setdefault(key, item)
    return list(unique.values())


def _homework_for_student(
    homework: list[dict[str, Any]], student_id: int
) -> list[dict[str, Any]]:
    """Filter homework again because some servers ignore the query filter."""
    result = []
    for item in homework:
        additional = item.get("additionalObjects") or {}
        student_ids = _ids_from_wrapper(additional.get("leerlingen"))

        checks = additional.get("swigemaaktVinkjes") or {}
        check_items = checks.get("items", []) if isinstance(checks, dict) else checks
        for check in check_items or []:
            if not isinstance(check, dict):
                continue
            student = check.get("leerling")
            if isinstance(student, dict) and (check_id := link_id(student)) is not None:
                student_ids.add(check_id)

        direct = additional.get("huiswerkgemaakt")
        if isinstance(direct, dict):
            student = direct.get("leerling")
            if isinstance(student, dict) and (direct_id := link_id(student)) is not None:
                student_ids.add(direct_id)

        # No student metadata means this is a general assignment. In that case
        # retain it because the endpoint was requested for this exact student.
        if not student_ids or student_id in student_ids:
            result.append(item)
    return result


def _ids_from_wrapper(wrapper: Any) -> set[int]:
    """Extract learner ids from a SOMtoday LinkableWrapper or list."""
    if isinstance(wrapper, dict):
        items = wrapper.get("items") or []
    elif isinstance(wrapper, list):
        items = wrapper
    else:
        items = []
    return {
        item_id
        for item in items
        if isinstance(item, dict) and (item_id := link_id(item)) is not None
    }
