import requests
import os
from email.message import EmailMessage
from config import settings  # Importamos la configuración validada por Pydantic

def _send_email(to: str, subject: str, plain: str, html: str) -> None:
    # Esta es tu nueva "llave" que pondrás en Railway
    api_key = os.getenv("RESEND_API_KEY") 
    
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "Sistema Recibos <jefatura.nominas@zapatamorelos.gob.mx>", # Luego lo cambias por el institucional
                "to": [to],
                "subject": subject,
                "html": html,
                "text": plain,
            }
        )
        if r.status_code in [200, 201]:
            print(f"✅ CORREO ENVIADO vía API a {to}")
        else:
            print(f"❌ ERROR API RESEND: {r.text}")
            
    except Exception as e:
        print(f"❌ ERROR CRÍTICO EN API: {str(e)}")

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