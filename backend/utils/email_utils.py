import smtplib
import ssl
from email.message import EmailMessage
from config import settings  # Importamos la configuración validada por Pydantic

def _send_email(to: str, subject: str, plain: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to

    # Cuerpo del mensaje
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        # Lógica para Puerto 465 (SSL Directo)
        if settings.smtp_port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_server, settings.smtp_port, context=context, timeout=15) as smtp:
                if settings.smtp_debug:
                    smtp.set_debuglevel(1)
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        
        # Lógica para Puerto 587 (STARTTLS) - Recomendado para Gmail
        else:
            with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_debug:
                    smtp.set_debuglevel(1)
                
                # Inicia TLS por seguridad
                smtp.starttls(context=ssl.create_default_context())
                
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        
        print(f"✅ DEBUG: Correo enviado exitosamente a {to}")

    except smtplib.SMTPAuthenticationError:
        print("❌ ERROR SMTP: Autenticación fallida. Revisa el usuario y la Contraseña de Aplicación.")
    except Exception as e:
        print(f"❌ ERROR CRÍTICO SMTP: No se pudo enviar el correo a {to}. Detalle: {str(e)}")

def enviar_correo_verificacion(destino: str, enlace: str) -> None:
    link = enlace.strip()
    plain = f"Hola,\n\nHaz clic en el siguiente enlace para verificar tu correo:\n\n{link}\n\nAtentamente,\nSYSTESO - Ayuntamiento de Emiliano Zapata"
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
    plain = f"Hola,\n\nSolicitaste restablecer tu contraseña. Abre este enlace:\n\n{link}\n\nSi no fuiste tú, ignora este mensaje."
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
  <p>Atentamente,<br>SYSTESO - Ayuntamiento de Emiliano Zapata</p>
</body></html>"""
    _send_email(destino, "Recuperación de contraseña - Recibos Ayuntamiento", plain, html)