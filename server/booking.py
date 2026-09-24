"""Google Calendar booking: list free slots and create an appointment with an invite.

Uses the calendar owner's own Google account via OAuth (see authorize_google.py), so
the event is created as them and Google emails the invite to the caller. A service
account can't invite attendees without Workspace admin setup, which is why OAuth.

The Google client library is synchronous, so every call runs in a worker thread to
keep the voice pipeline responsive.
"""

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
SERVER_DIR = Path(__file__).parent
TOKEN_FILE = Path(os.getenv("GOOGLE_TOKEN_FILE", SERVER_DIR / "google_token.json"))
CREDENTIALS_FILE = Path(os.getenv("GOOGLE_CREDENTIALS_FILE", SERVER_DIR / "google_credentials.json"))
MIN_NOTICE = timedelta(hours=1)  # earliest slot offered, from now
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class BookingError(Exception):
    pass


def is_configured() -> bool:
    return TOKEN_FILE.exists()


def calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


@dataclass
class BookableHours:
    days: set[int]  # 0=Mon ... 6=Sun
    start: time
    end: time


def parse_bookable_hours(text: str) -> BookableHours:
    """Parse "Mon-Fri 09:00-17:00" or "Mon,Wed,Fri 9:00-14:30"."""
    m = re.fullmatch(r"\s*([A-Za-z,\- ]+?)\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", text)
    if not m:
        raise ValueError(f"Bookable hours must look like 'Mon-Fri 09:00-17:00', got {text!r}")
    days: set[int] = set()
    for token in m.group(1).replace(" ", "").split(","):
        if "-" in token:
            a, b = (WEEKDAYS.index(x[:3].lower()) for x in token.split("-", 1))
            days |= {d % 7 for d in range(a, a + ((b - a) % 7) + 1)}
        else:
            days.add(WEEKDAYS.index(token[:3].lower()))
    return BookableHours(
        days, time(int(m.group(2)), int(m.group(3))), time(int(m.group(4)), int(m.group(5)))
    )


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise BookingError("Google token invalid; run authorize_google.py again")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _busy_intervals(day_start: datetime, day_end: datetime) -> list[tuple[datetime, datetime]]:
    result = (
        _service()
        .events()
        .list(
            calendarId=calendar_id(),
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
        .execute()
    )
    busy = []
    for ev in result.get("items", []):
        if ev.get("transparency") == "transparent" or ev.get("status") == "cancelled":
            continue
        start, end = ev["start"].get("dateTime"), ev["end"].get("dateTime")
        if not start or not end:  # all-day events don't block bookings
            continue
        busy.append((datetime.fromisoformat(start), datetime.fromisoformat(end)))
    return busy


def _free_slots(
    day: date_type, tz: ZoneInfo, hours: BookableHours, minutes: int, limit: int
) -> list[dict[str, str]]:
    if day.weekday() not in hours.days:
        return []
    day_start = datetime.combine(day, hours.start, tz)
    day_end = datetime.combine(day, hours.end, tz)
    busy = _busy_intervals(day_start, day_end)
    earliest = datetime.now(tz) + MIN_NOTICE
    step = timedelta(minutes=minutes)

    slots = []
    cursor = day_start
    while cursor + step <= day_end and len(slots) < limit:
        slot_end = cursor + step
        clash = any(b_start < slot_end and b_end > cursor for b_start, b_end in busy)
        if cursor >= earliest and not clash:
            slots.append({"start": cursor.isoformat(), "spoken": _spoken(cursor)})
        cursor += step
    return slots


def _spoken(dt: datetime) -> str:
    return dt.strftime("%A %B %d at %I:%M %p").replace(" 0", " ").lstrip("0")


async def get_slots(
    date: str, timezone: str, hours_text: str, minutes: int, limit: int = 6
) -> list[dict[str, str]]:
    """Free slots on `date` (YYYY-MM-DD) as {"start": ISO, "spoken": "Monday ... at 9:00 AM"}."""
    day = datetime.strptime(date, "%Y-%m-%d").date()
    hours = parse_bookable_hours(hours_text)
    return await asyncio.to_thread(_free_slots, day, ZoneInfo(timezone), hours, minutes, limit)


def _insert_event(body: dict) -> dict:
    return (
        _service()
        .events()
        .insert(calendarId=calendar_id(), body=body, sendUpdates="all")
        .execute()
    )


async def create_booking(
    start: str,
    name: str,
    email: str,
    timezone: str,
    minutes: int,
    title: str,
    phone: str = "",
    business: str = "",
    notes: str = "",
) -> dict:
    """Create the event and email the invite. Returns {"spoken", "link", "id"}."""
    tz = ZoneInfo(timezone)
    start_dt = datetime.fromisoformat(start).astimezone(tz)
    end_dt = start_dt + timedelta(minutes=minutes)
    description = "\n".join(
        s for s in [f"Booked by the AI voice assistant.", f"Name: {name}",
                    f"Business: {business}" if business else "",
                    f"Phone: {phone}" if phone else "", f"Email: {email}",
                    f"Notes: {notes}" if notes else ""] if s
    )
    body = {
        "summary": f"{title} - {name}" + (f" ({business})" if business else ""),
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
        "attendees": [{"email": email, "displayName": name}],
        "reminders": {"useDefault": True},
    }
    event = await asyncio.to_thread(_insert_event, body)
    logger.info(f"Booked {name} <{email}> at {start_dt} (id={event.get('id')})")
    return {"spoken": _spoken(start_dt), "link": event.get("htmlLink", ""), "id": event.get("id")}
