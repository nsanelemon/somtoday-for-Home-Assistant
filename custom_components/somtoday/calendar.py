"""Calendar entities for SOMtoday lessons and homework."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import link_id
from .const import DOMAIN
from .coordinator import SomtodayCoordinator
from .sensor import homework_date, homework_details, lesson_details, parse_dt, student_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SomtodayCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[CalendarEntity] = []
    for index, student in enumerate(coordinator.data.get("students", [])):
        student_id = link_id(student)
        if student_id is None:
            continue
        legacy = index == 0
        entities.extend([
            SomtodayScheduleCalendar(coordinator, entry, student_id, legacy),
            SomtodayHomeworkCalendar(coordinator, entry, student_id, legacy),
        ])
    async_add_entities(entities)


class SomtodayCalendarBase(CoordinatorEntity[SomtodayCoordinator], CalendarEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, student_id: int, legacy: bool) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.student_id = student_id
        student = self.student_data.get("student") or {}
        device_key = entry.entry_id if legacy else f"{entry.entry_id}_{student_id}"
        prefix = entry.entry_id if legacy else f"{entry.entry_id}_{student_id}"
        self._uid_prefix = prefix
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=student_name(student) or entry.title,
            manufacturer="SOMtoday",
            model="Cloud account",
        )

    @property
    def student_data(self) -> dict[str, Any]:
        return self.coordinator.data.get("by_student", {}).get(str(self.student_id), {})

    def _events(self) -> list[CalendarEvent]:
        raise NotImplementedError

    @property
    def event(self) -> CalendarEvent | None:
        events = self._events()
        now = dt_util.now()
        today = now.date()
        active = []
        future = []
        for calendar_event in events:
            if isinstance(calendar_event.start, datetime):
                if calendar_event.start <= now < calendar_event.end:
                    active.append(calendar_event)
                elif calendar_event.start >= now:
                    future.append(calendar_event)
            else:
                if calendar_event.start <= today < calendar_event.end:
                    active.append(calendar_event)
                elif calendar_event.start >= today:
                    future.append(calendar_event)
        candidates = active or future
        return min(candidates, key=lambda item: item.start) if candidates else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return cached events overlapping the requested interval."""
        result = []
        for calendar_event in self._events():
            if isinstance(calendar_event.start, datetime):
                if calendar_event.end > start_date and calendar_event.start < end_date:
                    result.append(calendar_event)
            elif calendar_event.end > start_date.date() and calendar_event.start < end_date.date():
                result.append(calendar_event)
        return result


class SomtodayScheduleCalendar(SomtodayCalendarBase):
    _attr_name = "Rooster"
    _attr_icon = "mdi:calendar-school"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = f"{self._uid_prefix}_calendar_schedule"

    def _events(self) -> list[CalendarEvent]:
        events = []
        for item in self.student_data.get("schedule", []):
            start = parse_dt(item.get("beginDatumTijd"))
            end = parse_dt(item.get("eindDatumTijd"))
            if not start or item.get("afspraakStatus") == "GEANNULEERD":
                continue
            details = lesson_details(item)
            summary = details["vak"] or details["titel"] or "Les"
            description = _description([
                ("Titel", details["titel"]),
                ("Lesuur", details["lesuur"]),
                ("Docent", details["docent"]),
                ("Omschrijving", details["omschrijving"]),
            ])
            events.append(CalendarEvent(
                start=start,
                end=end or start + timedelta(hours=1),
                summary=summary,
                location=details["lokaal"],
                description=description,
                uid=str(link_id(item)) if link_id(item) is not None else None,
            ))
        return sorted(events, key=lambda item: item.start)


class SomtodayHomeworkCalendar(SomtodayCalendarBase):
    _attr_name = "Huiswerk"
    _attr_icon = "mdi:book-clock"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = f"{self._uid_prefix}_calendar_homework"

    def _events(self) -> list[CalendarEvent]:
        events = []
        for item in self.student_data.get("homework", []):
            due = homework_date(item)
            if not due:
                continue
            details = homework_details(item, self.student_id)
            subject = details["vak"] or "Huiswerk"
            topic = details["onderwerp"]
            summary = f"{subject}: {topic}" if topic else subject
            if details["gemaakt"] is True:
                summary = f"KLAAR - {summary}"
            description = _description([
                ("Type", details["type"]),
                ("Status", "gemaakt" if details["gemaakt"] is True else "open"),
                ("Omschrijving", details["omschrijving"]),
            ])
            events.append(CalendarEvent(
                start=due,
                end=due + timedelta(days=1),
                summary=summary,
                description=description,
                uid=str(link_id(item)) if link_id(item) is not None else None,
            ))
        return sorted(events, key=lambda item: item.start)


def _description(values: list[tuple[str, Any]]) -> str | None:
    lines = [f"{label}: {value}" for label, value in values if value not in (None, "", [])]
    return "\n".join(lines) or None
