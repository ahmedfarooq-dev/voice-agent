"""Email notifications to the client's team via Brevo's HTTPS API.

Used for callback messages and new bookings. Sent in the background so the caller never
waits; a failure is logged (and the message/booking still exists on disk / in the
calendar) but is never spoken to the caller.

Brevo: free 300 emails/day. HTTPS API rather than SMTP because some hosts (DigitalOcean
among them) block outbound SMTP ports.
"""

import asyncio
import os
from datetime import datetime

import aiohttp
from loguru import logger

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT = aiohttp.ClientTimeout(total=10)
_background: set[asyncio.Task] = set()  # keep references so tasks aren't garbage-collected


def is_configured() -> bool:
    return bool(os.getenv("BREVO_API_KEY") and os.getenv("NOTIFY_TO") and os.getenv("NOTIFY_FROM_EMAIL"))


def _recipients() -> list[dict[str, str]]:
    return [{"email": e.strip()} for e in os.getenv("NOTIFY_TO", "").split(",") if e.strip()]


async def send_email(subject: str, text: str) -> bool:
    """Send one plain-text email to every NOTIFY_TO address. Returns True on success."""
    if not is_configured():
        logger.warning(f"Email not configured; not sent: {subject}")
        return False
    payload = {
        "sender": {
            "email": os.environ["NOTIFY_FROM_EMAIL"],
            "name": os.getenv("NOTIFY_FROM_NAME", "Voice Agent"),
        },
        "to": _recipients(),
        "subject": subject,
        "textContent": text,
    }
    headers = {"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(BREVO_URL, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status not in (200, 201, 202):
                    logger.error(f"Brevo error {resp.status}: {body}")
                    return False
        logger.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email failed ({subject}): {e}")
        return False


def send_in_background(subject: str, text: str) -> None:
    """Fire-and-forget from inside the voice pipeline (falls back to a blocking send
    when called with no event loop running, e.g. from a script)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(send_email(subject, text))
        return
    task = loop.create_task(send_email(subject, text))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _stamp() -> str:
    return datetime.now().strftime("%a %d %b %Y, %I:%M %p")


def notify_message(company: str, name: str, phone: str, message: str) -> None:
    send_in_background(
        f"[{company}] Callback request from {name}",
        f"A caller asked for a callback via the voice agent.\n\n"
        f"Name:     {name}\nPhone:    {phone}\nMessage:  {message}\n\nReceived: {_stamp()}\n",
    )


def notify_booking(
    company: str, name: str, when: str, email: str, phone: str, business: str, link: str
) -> None:
    send_in_background(
        f"[{company}] New booking: {name}, {when}",
        f"The voice agent booked an appointment.\n\n"
        f"When:     {when}\nName:     {name}\n"
        + (f"Business: {business}\n" if business else "")
        + f"Email:    {email}\nPhone:    {phone}\n"
        + (f"Calendar: {link}\n" if link else "")
        + f"\nBooked:   {_stamp()}\n",
    )


if __name__ == "__main__":
    ok = asyncio.run(send_email("Voice agent test email", f"If you can read this, email works. {_stamp()}"))
    print("sent" if ok else "FAILED (see log above)")
