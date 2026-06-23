"""Transactional email via Resend's HTTP API.

Provider-isolated and SDK-free — uses `requests` directly, mirroring
apps/billing/dodo.py, so there's no extra dependency to install.

Email is optional. When RESEND_API_KEY is unset, send_email() logs and no-ops,
so local/dev runs work without email configured. Sends never raise into the
caller: a failed email must not 500 a password-reset request or roll back a
billing grant, so failures are logged and swallowed (send_email returns False).

The From address (RESEND_FROM_EMAIL) must use a domain verified in the Resend
dashboard — verify getpulsecharts.com there before this goes live, or delivery
will be rejected.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger("email")

_RESEND_ENDPOINT = "https://api.resend.com/emails"

# Email is "configured" once an API key is present.
EMAIL_ENABLED = bool(settings.RESEND_API_KEY)


def send_email(*, to, subject: str, html: str, text: str = "", reply_to: str = "") -> bool:
    """Send one transactional email. Returns True on success, False otherwise
    (including when email is not configured). Never raises."""
    if not EMAIL_ENABLED:
        logger.info("Email disabled (no RESEND_API_KEY); skipped %r to %s", subject, to)
        return False

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [to] if isinstance(to, str) else list(to),
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        resp = requests.post(
            _RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("Resend send failed (network) to %s: %s", to, exc)
        return False

    if resp.status_code >= 400:
        logger.warning(
            "Resend send failed (%s) to %s: %s", resp.status_code, to, resp.text[:300]
        )
        return False

    logger.info("Email sent: %r to %s", subject, to)
    return True


# --- Branded HTML shell ----------------------------------------------------

def _wrap(title: str, body_html: str) -> str:
    """Wrap inner content in a minimal, email-client-safe branded layout."""
    return f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:32px 0;">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e6e9ee;">
        <tr><td style="padding:24px 32px;border-bottom:1px solid #eef1f4;">
          <span style="font-size:18px;font-weight:700;color:#0f172a;">📈 PulseCharts</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="margin:0 0 16px;font-size:20px;color:#0f172a;">{title}</h1>
          {body_html}
        </td></tr>
        <tr><td style="padding:20px 32px;border-top:1px solid #eef1f4;color:#94a3b8;font-size:12px;">
          PulseCharts · Informational tool, not financial advice.<br>
          You received this email because of activity on your PulseCharts account.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _button(label: str, url: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:#2563eb;color:#ffffff;'
        f'text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:600;'
        f'font-size:15px;">{label}</a>'
    )


# --- Specific emails -------------------------------------------------------

def send_password_reset_email(*, to: str, reset_link: str) -> bool:
    """Password-reset email with the tokenized reset link."""
    body = f"""
      <p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">
        We received a request to reset the password for your PulseCharts account.
        Click the button below to choose a new password. This link expires shortly
        for your security.
      </p>
      <p style="margin:0 0 24px;">{_button("Reset your password", reset_link)}</p>
      <p style="margin:0 0 8px;color:#64748b;font-size:13px;line-height:1.6;">
        If the button doesn't work, paste this link into your browser:<br>
        <a href="{reset_link}" style="color:#2563eb;word-break:break-all;">{reset_link}</a>
      </p>
      <p style="margin:16px 0 0;color:#94a3b8;font-size:13px;">
        Didn't request this? You can safely ignore this email — your password
        won't change.
      </p>
    """
    text = (
        "Reset your PulseCharts password using this link (it expires shortly):\n"
        f"{reset_link}\n\nDidn't request this? Ignore this email; nothing changes."
    )
    return send_email(
        to=to,
        subject="Reset your PulseCharts password",
        html=_wrap("Reset your password", body),
        text=text,
    )


def send_payment_confirmation_email(*, to: str, plan_label: str, renewal=None) -> bool:
    """Confirmation email after a successful payment / plan grant."""
    renewal_line = ""
    if renewal is not None:
        renewal_line = (
            f'<p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">'
            f'Your access runs through <strong>{renewal:%B %d, %Y}</strong>.</p>'
        )
    dashboard_url = f"{settings.FRONTEND_URL}/app"
    body = f"""
      <p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">
        Thanks for your payment — your <strong>{plan_label}</strong> plan is now
        active. You've unlocked premium indicators, saved layouts, and the trading
        signals feed.
      </p>
      {renewal_line}
      <p style="margin:0 0 24px;">{_button("Open your dashboard", dashboard_url)}</p>
      <p style="margin:0;color:#94a3b8;font-size:13px;line-height:1.6;">
        Need a receipt or have a billing question? Just reply to this email.
      </p>
    """
    text = (
        f"Thanks for your payment — your {plan_label} plan is now active.\n"
        f"Open your dashboard: {dashboard_url}"
    )
    return send_email(
        to=to,
        subject=f"Your PulseCharts {plan_label} plan is active",
        html=_wrap("Payment confirmed 🎉", body),
        text=text,
    )


def send_contact_message(*, to: str, from_email: str, message: str) -> bool:
    """Deliver a landing-page contact-us message to the support inbox.

    `to` is the support inbox (CONTACT_US_EMAIL); `from_email` is the visitor's
    address, set as reply_to so support can reply to them directly. The mail
    itself is still sent from the verified RESEND_FROM_EMAIL domain (you can't
    spoof an arbitrary From with Resend)."""
    safe_msg = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ).replace("\n", "<br>")
    body = f"""
      <p style="margin:0 0 8px;color:#334155;font-size:15px;">
        New message from the PulseCharts landing page chat.
      </p>
      <p style="margin:0 0 16px;color:#64748b;font-size:14px;">
        From: <a href="mailto:{from_email}" style="color:#2563eb;">{from_email}</a>
      </p>
      <div style="background:#f8fafc;border:1px solid #e6e9ee;border-radius:8px;
                  padding:16px;color:#0f172a;font-size:15px;line-height:1.6;">
        {safe_msg}
      </div>
    """
    text = f"Contact message from {from_email}:\n\n{message}"
    return send_email(
        to=to,
        subject=f"PulseCharts contact: {from_email}",
        html=_wrap("New contact message", body),
        text=text,
        reply_to=from_email,
    )
