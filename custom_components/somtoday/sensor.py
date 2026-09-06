"""Sensors for SOMtoday."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import link_id
from .const import DOMAIN
from .coordinator import SomtodayCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SomtodayCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[SensorEntity] = []
    for index, student in enumerate(coordinator.data.get("students", [])):
        student_id = link_id(student)
        if student_id is None:
            continue
        legacy = index == 0
        entities.extend([
            SomtodayStudentSensor(coordinator, entry, student_id, legacy),
            SomtodayLessonsTodaySensor(coordinator, entry, student_id, legacy),
            SomtodayNextLessonSensor(coordinator, entry, student_id, legacy),
            SomtodayHomeworkSensor(coordinator, entry, student_id, legacy),
            SomtodayHomeworkWindowSensor(coordinator, entry, student_id, legacy, "tomorrow"),
            SomtodayHomeworkWindowSensor(coordinator, entry, student_id, legacy, "week"),
            SomtodayLatestGradeSensor(coordinator, entry, student_id, legacy),
        ])
    async_add_entities(entities)


class SomtodaySensorBase(CoordinatorEntity[SomtodayCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SomtodayCoordinator, entry: ConfigEntry, student_id: int, legacy: bool) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.student_id = student_id
        self.legacy = legacy
        student = self.student_data.get("student") or {}
        device_key = entry.entry_id if legacy else f"{entry.entry_id}_{student_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=student_name(student) or entry.title,
            manufacturer="SOMtoday",
            model="Cloud account",
        )

    @property
    def student_data(self) -> dict[str, Any]:
        return self.coordinator.data.get("by_student", {}).get(str(self.student_id), {})

    def _uid(self, suffix: str) -> str:
        prefix = self.entry.entry_id if self.legacy else f"{self.entry.entry_id}_{self.student_id}"
        return f"{prefix}_{suffix}"


class SomtodayStudentSensor(SomtodaySensorBase):
    _attr_name = "Leerling"
    _attr_icon = "mdi:account-school"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = self._uid("student")

    @property
    def native_value(self):
        return student_name(self.student_data.get("student") or {}) or None

    @property
    def extra_state_attributes(self):
        student = self.student_data.get("student") or {}
        return {
            "somtoday_id": self.student_id,
            "leerlingnummer": student.get("leerlingnummer"),
            "email": student.get("email"),
            "leerlingen_in_account": len(self.coordinator.data.get("students", [])),
            "huiswerk_ontvangen": self.student_data.get("homework_received"),
            "huiswerk_na_leerlingfilter": self.student_data.get(
                "homework_after_student_filter"
            ),
            "api_fouten": self.student_data.get("errors") or None,
        }


class SomtodayLessonsTodaySensor(SomtodaySensorBase):
    _attr_name = "Lessen vandaag"
    _attr_icon = "mdi:calendar-today"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = self._uid("lessons_today")

    def _today_items(self):
        today = dt_util.now().date()
        return [item for item in self.student_data.get("schedule", []) if (start := parse_dt(item.get("beginDatumTijd"))) and start.date() == today and item.get("afspraakStatus") != "GEANNULEERD"]

    @property
    def native_value(self):
        return len(self._today_items())

    @property
    def extra_state_attributes(self):
        return {"lessen": [lesson_details(item) for item in self._today_items()]}


class SomtodayNextLessonSensor(SomtodaySensorBase):
    _attr_name = "Volgende les"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = self._uid("next_lesson")

    def _next(self):
        now = dt_util.now()
        future = [(start, item) for item in self.student_data.get("schedule", []) if (start := parse_dt(item.get("beginDatumTijd"))) and start >= now and item.get("afspraakStatus") != "GEANNULEERD"]
        return min(future, key=lambda value: value[0])[1] if future else None

    @property
    def native_value(self):
        item = self._next()
        if not item:
            return "Geen"
        details = lesson_details(item)
        return details["vak"] or details["titel"] or "Les"

    @property
    def extra_state_attributes(self):
        item = self._next()
        return lesson_details(item) if item else {}


class SomtodayHomeworkSensor(SomtodaySensorBase):
    _attr_name = "Huiswerk openstaand"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = self._uid("homework")

    @property
    def native_value(self):
        return len(open_homework(self.student_data.get("homework", []), self.student_id))

    @property
    def extra_state_attributes(self):
        items = open_homework(self.student_data.get("homework", []), self.student_id)
        return {"items": [homework_details(item, self.student_id) for item in items[:25]]}


class SomtodayHomeworkWindowSensor(SomtodaySensorBase):
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator, entry, student_id, legacy, window: str):
        super().__init__(coordinator, entry, student_id, legacy)
        self.window = window
        if window == "tomorrow":
            self._attr_name = "Huiswerk morgen"
            self._attr_unique_id = self._uid("homework_tomorrow")
        else:
            self._attr_name = "Huiswerk komende 7 dagen"
            self._attr_unique_id = self._uid("homework_week")

    def _items(self) -> list[dict[str, Any]]:
        today = dt_util.now().date()
        items = self.student_data.get("homework", [])
        if self.window == "tomorrow":
            start, end = today + timedelta(days=1), today + timedelta(days=2)
        else:
            start, end = today, today + timedelta(days=7)
        return sorted(
            [item for item in items if (due := homework_date(item)) and start <= due < end],
            key=lambda item: homework_date(item) or date.max,
        )

    @property
    def native_value(self):
        return sum(
            homework_done(item, self.student_id) is not True for item in self._items()
        )

    @property
    def extra_state_attributes(self):
        details = [
            homework_details(item, self.student_id) for item in self._items()[:25]
        ]
        open_count = sum(item["gemaakt"] is not True for item in details)
        attributes: dict[str, Any] = {
            "periode": "morgen" if self.window == "tomorrow" else "komende 7 dagen",
            "totaal": len(details),
            "openstaand": open_count,
            "afgerond": len(details) - open_count,
            "items": details,
        }
        # Scalar display fields are easy to import on ESPHome/LVGL devices.
        for index in range(5):
            detail = details[index] if index < len(details) else None
            attributes[f"item_{index + 1}_regel"] = _homework_display_line(detail)
            attributes[f"item_{index + 1}_detail"] = _homework_display_detail(detail)
        return attributes


class SomtodayLatestGradeSensor(SomtodaySensorBase):
    _attr_name = "Laatste cijfer"
    _attr_icon = "mdi:school"

    def __init__(self, coordinator, entry, student_id, legacy):
        super().__init__(coordinator, entry, student_id, legacy)
        self._attr_unique_id = self._uid("latest_grade")

    def _latest(self):
        grades = self.student_data.get("grades", [])
        dated = [(stamp, grade) for grade in grades if (stamp := parse_dt(grade.get("datumInvoer")))]
        return max(dated, key=lambda value: value[0])[1] if dated else (grades[0] if grades else None)

    @property
    def native_value(self):
        grade = self._latest()
        return (grade.get("geldendResultaat") or grade.get("resultaat")) if grade else None

    @property
    def extra_state_attributes(self):
        grade = self._latest()
        if not grade:
            return {}
        subject = grade.get("vak") or {}
        return {"vak": subject.get("naam"), "vak_afkorting": subject.get("afkorting"), "datum_invoer": grade.get("datumInvoer"), "type": grade.get("type"), "resultaat": grade.get("resultaat"), "geldend_resultaat": grade.get("geldendResultaat"), "telt_niet_mee": grade.get("teltNietmee"), "toets_niet_gemaakt": grade.get("toetsNietGemaakt")}


def student_name(student: dict[str, Any]) -> str:
    return " ".join(value for value in [student.get("roepnaam"), student.get("voorvoegsel"), student.get("achternaam")] if value)


def parse_dt(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt_util.as_local(parsed)
    except (TypeError, ValueError):
        return None


def lesson_details(item: dict[str, Any]) -> dict[str, Any]:
    extra = item.get("additionalObjects") or {}
    subject = extra.get("vak") or item.get("vak") or {}
    teachers = extra.get("docentAfkortingen") or item.get("docentAfkortingen")
    return {"titel": item.get("titel"), "vak": subject.get("naam") or subject.get("afkorting"), "vak_afkorting": subject.get("afkorting"), "start": item.get("beginDatumTijd"), "einde": item.get("eindDatumTijd"), "lokaal": item.get("locatie"), "lesuur": item.get("beginLesuur"), "eind_lesuur": item.get("eindLesuur"), "docent": teachers, "status": item.get("afspraakStatus"), "omschrijving": item.get("omschrijving")}


def homework_date(item: dict[str, Any]) -> date | None:
    value = item.get("datumTijd") or item.get("beginDatumTijd") or item.get("datum")
    parsed = parse_dt(value)
    if parsed:
        return parsed.date()
    week = item.get("weeknummer") or item.get("weekNummer")
    year = item.get("jaar") or dt_util.now().year
    try:
        return date.fromisocalendar(int(year), int(week), 1) if week else None
    except (TypeError, ValueError):
        return None


def homework_done(item: dict[str, Any], student_id: int) -> bool | None:
    additional = item.get("additionalObjects") or {}
    direct = additional.get("huiswerkgemaakt")
    if isinstance(direct, bool):
        return direct
    if isinstance(direct, dict) and isinstance(direct.get("gemaakt"), bool):
        return direct["gemaakt"]
    wrapper = additional.get("swigemaaktVinkjes") or {}
    checks = wrapper.get("items", []) if isinstance(wrapper, dict) else wrapper
    relevant = []
    for check in checks or []:
        if not isinstance(check, dict):
            continue
        learner = check.get("leerling") or {}
        check_id = link_id(learner) if isinstance(learner, dict) else None
        if check_id in (None, student_id):
            relevant.append(check.get("gemaakt"))
    if any(value is False for value in relevant):
        return False
    if relevant and all(value is True for value in relevant):
        return True
    return None


def homework_details(item: dict[str, Any], student_id: int) -> dict[str, Any]:
    swi = item.get("studiewijzerItem") or {}
    group = item.get("lesgroep") or {}
    subject = group.get("vak") or {}
    guide = item.get("studiewijzer") or {}
    due = homework_date(item)
    return {"datum": due.isoformat() if due else None, "vak": subject.get("naam") or subject.get("afkorting") or guide.get("naam"), "vak_afkorting": subject.get("afkorting"), "onderwerp": swi.get("onderwerp"), "omschrijving": swi.get("omschrijving"), "type": swi.get("huiswerkType"), "gemaakt": homework_done(item, student_id), "bron": item.get("_somtoday_source")}


def open_homework(items: list[dict[str, Any]], student_id: int) -> list[dict[str, Any]]:
    return sorted([item for item in items if homework_done(item, student_id) is not True], key=lambda item: homework_date(item) or date.max)


def _homework_display_line(detail: dict[str, Any] | None) -> str:
    if not detail:
        return ""
    status = "KLAAR" if detail["gemaakt"] is True else "OPEN"
    due = detail["datum"] or "zonder datum"
    subject = detail["vak"] or "Huiswerk"
    topic = detail["onderwerp"] or detail["type"] or ""
    return " | ".join(value for value in (status, due, subject, topic) if value)[:120]


def _homework_display_detail(detail: dict[str, Any] | None) -> str:
    if not detail:
        return "Geen huiswerk op deze regel."
    values = [
        f"Status: {'afgerond' if detail['gemaakt'] is True else 'openstaand'}",
        f"Datum: {detail['datum']}" if detail["datum"] else None,
        f"Vak: {detail['vak']}" if detail["vak"] else None,
        f"Onderwerp: {detail['onderwerp']}" if detail["onderwerp"] else None,
        f"Type: {detail['type']}" if detail["type"] else None,
        detail["omschrijving"],
    ]
    # Bound display strings to avoid excessive heap use on small ESPHome nodes.
    return "\n".join(value for value in values if value)[:600]
