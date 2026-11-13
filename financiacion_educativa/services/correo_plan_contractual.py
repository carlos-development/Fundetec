from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .correos import ConfiguracionSMTPInvalida, obtener_email_logo_url


def construir_correo_plan(*, outbox, connection=None):
    base = str(settings.BRAND_PUBLIC_BASE_URL).rstrip('/')
    partes = urlsplit(base)
    logo = obtener_email_logo_url()
    if (partes.scheme != 'https' or not partes.hostname or partes.username or partes.password
            or partes.query or partes.fragment or partes.path not in ('', '/') or not logo):
        raise ConfiguracionSMTPInvalida('El aviso requiere portal HTTPS y logo oficial configurados.')
    contexto = {'brand_name': 'Aprobado', 'email_logo_url': logo,
        'portal_url': base + reverse('financiacion_educativa_web:finanzas', args=[outbox.solicitud_id])}
    mensaje = EmailMultiAlternatives(subject='Tu plan contractual est\u00e1 disponible | Aprobado',
        body=render_to_string('emails/financiacion_educativa/plan_contractual_disponible.txt', contexto),
        from_email=settings.DEFAULT_FROM_EMAIL, to=outbox.destinatarios, connection=connection)
    mensaje.attach_alternative(render_to_string('emails/financiacion_educativa/plan_contractual_disponible.html', contexto), 'text/html')
    return mensaje
