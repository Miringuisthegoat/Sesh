from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app


class EmailDeliveryError(RuntimeError):
    pass


def send_invite_email(*, to_email: str, full_name: str, company_name: str, invite_url: str,
                      expires_hours: int) -> bool:
    config = current_app.config
    host = config["MAIL_SERVER"].strip()
    sender = config["MAIL_DEFAULT_SENDER"].strip()
    if not host and not sender:
        return False
    if not host or not sender:
        raise EmailDeliveryError("MAIL_SERVER and MAIL_DEFAULT_SENDER must both be configured.")
    if config["MAIL_USE_TLS"] and config["MAIL_USE_SSL"]:
        raise EmailDeliveryError("Configure either MAIL_USE_TLS or MAIL_USE_SSL, not both.")

    message = EmailMessage()
    message["Subject"] = f"Invitation to join {company_name} on Sesh"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        f"Hello {full_name},\n\n"
        f"{company_name} invited you to join its Sesh workspace.\n\n"
        f"Accept the invitation and choose your password here:\n{invite_url}\n\n"
        f"This link can only be used once and expires in {expires_hours} hours. "
        "If you were not expecting this invitation, you can ignore this email.\n"
    )

    try:
        context = ssl.create_default_context()
        if config["MAIL_USE_SSL"]:
            with smtplib.SMTP_SSL(host, config["MAIL_PORT"], timeout=config["MAIL_TIMEOUT"],
                                  context=context) as server:
                _send_message(server, message, config)
        else:
            with smtplib.SMTP(host, config["MAIL_PORT"], timeout=config["MAIL_TIMEOUT"]) as server:
                if config["MAIL_USE_TLS"]:
                    server.starttls(context=context)
                _send_message(server, message, config)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("SMTP invite delivery failed.") from exc
    return True


def _send_message(server, message: EmailMessage, config) -> None:
    username = config["MAIL_USERNAME"].strip()
    if username:
        server.login(username, config["MAIL_PASSWORD"])
    server.send_message(message)