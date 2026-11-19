"""Follow-up delivery.

Default mode ``sink``: messages go to the configured local SMTP sink (Mailpit), which captures
everything and delivers nothing. Real delivery (``smtp``) needs an explicit second switch
(``LEAD_DELIVERY_ALLOW_REAL_RECIPIENTS=true``); the settings refuse ``smtp`` without it.
"""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import Settings


class DeliveryError(Exception):
    pass


def send(settings: Settings, to: str, subject: str, body: str, reference: str) -> str:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message_id = make_msgid(idstring=uuid.uuid4().hex[:12], domain="lead-automation.local")
    message["Message-ID"] = message_id
    message["X-Lead-Reference"] = reference
    message["X-Lead-Delivery-Mode"] = settings.delivery_mode
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"{type(exc).__name__}") from exc
    return message_id
