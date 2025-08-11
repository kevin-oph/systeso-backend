# backend/utils/email_utils.py
import os
import smtplib
import ssl
from email.message import EmailMessage

# Lee TODO de variables de entorno (no hardcodees credenciales en el código)
EMAIL_ORIGEN = os.getenv("EMAIL_FROM", "jefatura.nominas@zapatamorelos.gob.mx")
SMTP_SERVER  = os.getenv("SMTP_SERVER", "mail.zapatamorelos.gob.mx")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "465"))            # Principal (SSL)
SMTP_FALLBACK_PORT = int(os.getenv("SMTP_FALLBACK_PORT", "587"))  # Secundario (STARTTLS)
EMAIL_USER   = os.getenv("EMAIL_USER", "jefatura.nominas@zapatamorelos.gob.mx")
EMAIL_PASS   = os.getenv("EMAIL_PASSWORD", "Admin123Nomina")
SMTP_DEBUG   = int(os.getenv("SMTP_DEBUG", "0"))

def _send_email(to: str, subject: str, plain: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to

    # Parte texto plano
    msg.set_content(plain)

    # Parte HTML
    msg.add_alternative(html, subtype="html")

    # Conecta por SSL (465) o STARTTLS (587)
    if SMTP_PORT == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as smtp:
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            try:
                smtp.starttls(context=ssl.create_default_context())
            except smtplib.SMTPException:
                # Algunos servidores en 25/2525 pueden no requerir STARTTLS
                pass
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)

def enviar_correo_verificacion(destino: str, enlace: str) -> None:
    link = enlace.strip()
    plain = f"""Hola,

Haz clic en el siguiente enlace para verificar tu correo:

<{link}>

Si el botón no funciona, copia y pega la URL en tu navegador.

Atentamente,
SYSTESO - Ayuntamiento de Emiliano Zapata
"""
    html = f"""<html><body>
  <p>Hola,</p>
  <p>Haz clic en el botón para verificar tu correo:</p>
  <p>
    <a href="{link}" style="background:#235B4E;color:#fff;padding:12px 18px;border-radius:6px;text-decoration:none;display:inline-block;">
      Verificar correo
    </a>
  </p>
  <p>Si el botón no funciona, copia y pega esta URL:<br>
    <a href="{link}">{link}</a>
  </p>
  <p>Atentamente,<br>SYSTESO - Ayuntamiento de Emiliano Zapata</p>
</body></html>"""
    _send_email(destino, "Confirma tu correo - Recibos Ayuntamiento", plain, html)

def enviar_correo_recuperacion(destino: str, enlace: str) -> None:
    link = enlace.strip()
    plain = f"""Hola,

Solicitaste restablecer tu contraseña. Abre este enlace:

<{link}>

Si no fuiste tú, ignora este mensaje.
"""
    html = f"""<html><body>
  <p>Hola,</p>
  <p>Solicitaste restablecer tu contraseña. Usa este botón:</p>
  <p>
    <a href="{link}" style="background:#235B4E;color:#fff;padding:12px 18px;border-radius:6px;text-decoration:none;display:inline-block;">
      Restablecer contraseña
    </a>
  </p>
  <p>Si el botón no funciona, copia y pega esta URL:<br>
    <a href="{link}">{link}</a>
  </p>
</body></html>"""
    _send_email(destino, "Recuperación de contraseña - Recibos Ayuntamiento", plain, html)
