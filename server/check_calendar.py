"""End-to-end booking check without the voice pipeline.

    python check_calendar.py            -> shows the calendar and the next free slots
    python check_calendar.py --book     -> also books a test slot for the calendar owner,
                                           waits for you to see the invite, then deletes it
"""

import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import booking
from prompt import appointment_minutes, appointment_title, bookable_hours, business_timezone


async def main() -> None:
    if not booking.is_configured():
        sys.exit("Not authorised yet: run authorize_google.py first.")

    res = booking._service().events().list(calendarId=booking.calendar_id(), maxResults=1).execute()
    owner, cal_tz = res.get("summary"), res.get("timeZone")
    print(f"Calendar: {owner} (calendar timezone {cal_tz}; bot uses {business_timezone()})")
    print(f"Bookable: {bookable_hours()} | {appointment_minutes()}-min '{appointment_title()}'")

    tz = ZoneInfo(business_timezone())
    today = datetime.now(tz).date()
    slots, day = [], None
    for i in range(1, 8):
        day = (today + timedelta(days=i)).isoformat()
        slots = await booking.get_slots(day, business_timezone(), bookable_hours(), appointment_minutes())
        if slots:
            break
    if not slots:
        sys.exit("No free slots found in the next 7 days; check bookable hours.")
    print(f"Next free slots on {day}: " + ", ".join(s["spoken"].split(" at ")[1] for s in slots))

    if "--book" not in sys.argv:
        print("Run with --book to create and delete a test event.")
        return

    result = await booking.create_booking(
        start=slots[0]["start"], name="Test Caller", email=owner, timezone=business_timezone(),
        minutes=appointment_minutes(), title=appointment_title(), phone="555 0100",
        business="Test Plumbing", notes="Created by check_calendar.py; will be deleted.",
    )
    print(f"Booked test event: {result['spoken']}\n  {result['link']}")
    input("Check your calendar and inbox for the invite, then press Enter to delete it... ")
    booking._service().events().delete(
        calendarId=booking.calendar_id(), eventId=result["id"], sendUpdates="all"
    ).execute()
    print("Deleted. Booking works end to end.")


if __name__ == "__main__":
    asyncio.run(main())
