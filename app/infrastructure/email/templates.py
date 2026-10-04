"""The emails Vocably sends, as subject + HTML + plain text.

Like the AI prompts, this is copy a learner reads: review a change to it as a
product change. Kept apart from the adapter that delivers it so swapping the
provider never touches the wording.

Email HTML is its own dialect — tables for layout, every style inline, no
external stylesheet, no image. Mail clients strip ``<style>`` and block remote
images by default, and a verification code that only appears once a logo has
loaded is a code some people never see. The wordmark is therefore text.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

#: The PWA's ``theme_color`` and ``background_color`` (vocably-mobile's
#: ``web/manifest.json``), so the email looks like the app that sent it.
_BRAND = "#2961CE"
_PAGE = "#EDF2FA"
_INK = "#16213A"
_MUTED = "#5B6781"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def link_code_email(code: str, ttl_minutes: int) -> RenderedEmail:
    """The code that proves an email address before it is added to an account.

    The subject carries no code: it is what shows on a lock screen. The plain
    text part is not a courtesy — it is what a watch, a screen reader and
    every "copy code" suggestion read.
    """
    minutes = f"{ttl_minutes} minute" + ("" if ttl_minutes == 1 else "s")
    text = (
        f"Your Vocably verification code is {code}\n\n"
        f"Enter it in the app to add this email address to your account. "
        f"It expires in {minutes}.\n\n"
        "If you didn't ask for this, you can ignore this email — nothing changes "
        "unless the code is entered. Vocably will never ask you for this code by "
        "phone, chat or email, so don't share it with anyone.\n"
    )
    html = f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your Vocably verification code</title>
</head>
<body style="margin:0;padding:0;background:{_PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">\
Use this code to add your email to Vocably. It expires in {minutes}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="background:{_PAGE};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="max-width:440px;background:#ffffff;border-radius:16px;">
<tr><td style="padding:32px 32px 8px;font-family:{_FONT};font-size:22px;\
font-weight:700;letter-spacing:-0.01em;color:{_BRAND};">Vocably</td></tr>
<tr><td style="padding:8px 32px 0;font-family:{_FONT};font-size:16px;\
line-height:24px;color:{_INK};">Enter this code in the app to add this email \
address to your account.</td></tr>
<tr><td style="padding:24px 32px 8px;">
<div style="background:{_PAGE};border-radius:12px;padding:18px 12px;\
text-align:center;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,\
monospace;font-size:32px;font-weight:700;letter-spacing:0.3em;color:{_INK};">\
{escape(code)}</div>
</td></tr>
<tr><td style="padding:8px 32px 0;font-family:{_FONT};font-size:14px;\
line-height:20px;color:{_MUTED};">This code expires in {minutes} and can be \
used once.</td></tr>
<tr><td style="padding:24px 32px 32px;font-family:{_FONT};font-size:13px;\
line-height:19px;color:{_MUTED};">\
<strong style="color:{_INK};">Didn't ask for this?</strong> You can ignore this \
email — nothing changes unless the code is entered. Vocably will never ask you \
for this code by phone, chat or email, so don't share it with anyone.</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
    return RenderedEmail(subject="Your Vocably verification code", html=html, text=text)
