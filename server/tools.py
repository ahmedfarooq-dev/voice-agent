"""Functions the LLM can call during the conversation (function calling).

Pipecat builds each tool's schema from the function name, type hints and docstring,
so the docstrings below are what the LLM reads to decide when and how to call them.
"""

import json
from datetime import datetime
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

import booking
import notify
from prompt import (
    appointment_minutes,
    appointment_title,
    bookable_hours,
    business_timezone,
    company_name,
)

MESSAGES_FILE = Path(__file__).parent / "data" / "messages.jsonl"


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


async def check_availability(params: FunctionCallParams, date: str):
    """Check which appointment times are free on a given day.

    Args:
        date: The day to check, in YYYY-MM-DD format.
    """
    try:
        slots = await booking.get_slots(
            date, business_timezone(), bookable_hours(), appointment_minutes()
        )
    except Exception as e:
        logger.error(f"check_availability failed: {e}")
        await params.result_callback(
            {"error": "Calendar unavailable. Apologise and offer to take a message instead."}
        )
        return
    if not slots:
        await params.result_callback(
            {"date": date, "slots": [], "note": "No free times that day. Suggest another day."}
        )
    else:
        await params.result_callback({"date": date, "slots": slots})


async def book_appointment(
    params: FunctionCallParams,
    start: str,
    name: str,
    email: str,
    phone: str,
    business_name: str = "",
):
    """Book the appointment. Only call after the caller confirmed the time, spelled-back email and read-back phone.

    Args:
        start: The exact "start" value of the chosen slot from check_availability.
        name: The caller's full name.
        email: The caller's email address, confirmed letter by letter. The invite is sent here.
        phone: The caller's phone number, confirmed digit by digit.
        business_name: The caller's business name, if they gave one.
    """
    if "@" not in email or "." not in email.split("@")[-1]:
        await params.result_callback(
            {"success": False, "error": "Email looks invalid. Ask the caller to spell it again."}
        )
        return
    if _digits(phone) < 7:
        await params.result_callback(
            {"success": False, "error": "No valid phone number. Ask the caller for it first."}
        )
        return
    try:
        result = await booking.create_booking(
            start=start,
            name=name,
            email=email,
            timezone=business_timezone(),
            minutes=appointment_minutes(),
            title=appointment_title(),
            phone=phone,
            business=business_name,
        )
    except Exception as e:
        logger.error(f"book_appointment failed: {e}")
        await params.result_callback(
            {"success": False, "error": "Booking failed. Apologise and offer to take a message."}
        )
        return
    notify.notify_booking(
        company_name(), name, result["spoken"], email, phone, business_name, result.get("link", "")
    )
    await params.result_callback(
        {"success": True, "when": result["spoken"], "note": "The calendar invite has been emailed."}
    )


async def take_message(params: FunctionCallParams, name: str, phone: str, message: str):
    """Save a message or callback request for the team.

    Args:
        name: The caller's name.
        phone: The caller's phone number, exactly as they said it.
        message: What the caller needs, including any preferred callback time.
    """
    # Guard against the LLM calling this before it actually collected a number.
    if _digits(phone) < 7:
        await params.result_callback(
            {"success": False, "error": "No valid phone number. Ask the caller for it first."}
        )
        return
    MESSAGES_FILE.parent.mkdir(exist_ok=True)
    record = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "name": name,
        "phone": phone,
        "message": message,
    }
    with MESSAGES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.info(f"Message saved: {record}")
    notify.notify_message(company_name(), name, phone, message)
    await params.result_callback({"success": True})


QUESTION_STARTS = (
    "what", "how", "when", "where", "why", "who", "which", "can", "could", "do", "does",
    "did", "is", "are", "will", "would", "should", "tell me", "any", "and",
)


def _last_user_text(params: FunctionCallParams) -> str:
    for message in reversed(params.context.messages):
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if isinstance(content, list):  # multimodal shape: list of parts
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content.strip()
    return ""


def _looks_like_question(text: str) -> bool:
    t = text.lower().strip()
    return t.endswith("?") or t.startswith(QUESTION_STARTS)


async def end_call(params: FunctionCallParams):
    """End the conversation. Only after the caller's LAST message was a goodbye or "that's all". Says goodbye for you."""
    # Guard: after a goodbye the model tends to hang up on the next thing the caller
    # says, even a question. If the last thing they said looks like a question, refuse
    # and let the model answer it (the LLM runs again on this result).
    last = _last_user_text(params)
    if _looks_like_question(last):
        await params.result_callback(
            {"success": False, "error": "The caller just asked something. Answer it; only end the call after they say goodbye."}
        )
        return
    # run_llm=False: no extra LLM turn after this result. The goodbye is pushed first
    # and EndWorkerFrame after it (both downstream), so the goodbye finishes playing
    # before the session closes.
    await params.result_callback(
        {"success": True}, properties=FunctionCallResultProperties(run_llm=False)
    )
    await params.llm.push_frame(
        TTSSpeakFrame(f"Thanks for contacting {company_name()}. Have a great day, goodbye!")
    )
    await params.llm.push_frame(EndWorkerFrame())


def get_tools() -> list:
    tools = [take_message, end_call]
    if booking.is_configured():
        tools = [check_availability, book_appointment, *tools]
    return tools
