# -*- coding: utf-8 -*-
"""Envoi d'emails (demandes modo, support). Fallback : data/outbox/."""
import os
import smtplib
import time
from email.mime.text import MIMEText

import config

_OUTBOX = os.path.join(os.path.dirname(__file__), "..", "data", "outbox")


def send(subject, body, to=None):
    to = to or config.ADMIN_EMAIL
    if config.SMTP_HOST and config.SMTP_USER:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = config.SMTP_USER
            msg["To"] = to
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as s:
                s.starttls()
                s.login(config.SMTP_USER, config.SMTP_PASS)
                s.sendmail(config.SMTP_USER, [to], msg.as_string())
            return True
        except Exception as e:
            print("[Mailer] SMTP KO:", e)
    os.makedirs(_OUTBOX, exist_ok=True)
    p = os.path.join(_OUTBOX, "mail_%d.txt" % int(time.time() * 1000))
    with open(p, "w", encoding="utf-8") as f:
        f.write("To: %s\nSubject: %s\n\n%s\n" % (to, subject, body))
    print("[Mailer] Email en file d'attente ->", p)
    return False
