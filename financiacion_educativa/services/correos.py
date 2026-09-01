from dataclasses import dataclass
from email.utils import parseaddr
import smtplib
import socket
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.urls import reverse

from financiacion_educativa.choices import TipoDecisionRevisionEducativa


SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
SAFE_ROUTING_SMTP_BACKEND = (
    'aprobado_web.email_backends.SafeRoutingEmailBackend'
)
SMTP_BACKENDS = frozenset({SMTP_BACKEND, SAFE_ROUTING_SMTP_BACKEND})
ASUNTO_CAPTURA_MOVIL = (
    'Continúa la captura de tu documento desde el celular | Aprobado'
)
ASUNTO_EXPEDIENTE_RECIBIDO = (
    'Recibimos tu expediente de financiacion educativa | Aprobado'
)
URL_MUESTRA_INERTE = 'https://example.invalid/educacion/muestra'


class ConfiguracionSMTPInvalida(ImproperlyConfigured):
    pass


def _presentacion_decision(tipo):
    presentaciones = {
        TipoDecisionRevisionEducativa.APPROVED: {
            'status_label': 'Expediente validado',
            'status_background': '#EAF7EE',
            'status_color': '#237A43',
            'status_border': '#33A55A',
            'next_step': (
                'Consulta el estado vigente en la plataforma. La solicitud '
                'solo continuara cuando corresponda segun el flujo educativo.'
            ),
        },
        TipoDecisionRevisionEducativa.REJECTED: {
            'status_label': 'Solicitud finalizada',
            'status_background': '#FDECEC',
            'status_color': '#A33A3A',
            'status_border': '#C94A4A',
            'next_step': (
                'Consulta el estado vigente en la plataforma si necesitas '
                'revisar la informacion comunicada.'
            ),
        },
        TipoDecisionRevisionEducativa.CORRECTION_REQUESTED: {
            'status_label': 'Accion requerida',
            'status_background': '#FFF3D6',
            'status_color': '#8A6A00',
            'status_border': '#E0A800',
            'next_step': (
                'Revisa la solicitud en la plataforma y completa el ajuste '
                'indicado para continuar.'
            ),
        },
    }
    return presentaciones[tipo]


def normalizar_destinatario(correo):
    normalizado = (correo or '').strip().casefold()
    try:
        validate_email(normalizado)
    except ValidationError as exc:
        raise ConfiguracionSMTPInvalida(
            'El destinatario configurado no es valido.'
        ) from exc
    return normalizado


def obtener_email_logo_url():
    url = str(getattr(settings, 'EDUCATION_EMAIL_LOGO_URL', '')).strip()
    partes = urlsplit(url)
    return url if partes.scheme == 'https' and partes.netloc else ''


def clasificar_error_entrega(error):
    if isinstance(error, ConfiguracionSMTPInvalida):
        return 'SMTP_CONFIGURATION_ERROR'
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'SMTP_AUTHENTICATION_ERROR'
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return 'SMTP_RECIPIENT_REFUSED'
    if isinstance(
        error,
        (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            socket.timeout,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return 'SMTP_CONNECTION_ERROR'
    return 'DELIVERY_BACKEND_ERROR'


def validar_configuracion_smtp():
    """Valida SMTP sin incluir valores ni credenciales en los errores."""
    errores = []
    backend = str(getattr(settings, 'EMAIL_BACKEND', '')).strip()
    host = str(getattr(settings, 'EMAIL_HOST', '')).strip()
    usuario = str(getattr(settings, 'EMAIL_HOST_USER', '')).strip()
    clave = str(getattr(settings, 'EMAIL_HOST_PASSWORD', ''))
    remitente = str(getattr(settings, 'DEFAULT_FROM_EMAIL', '')).strip()
    tls = bool(getattr(settings, 'EMAIL_USE_TLS', False))
    ssl = bool(getattr(settings, 'EMAIL_USE_SSL', False))

    try:
        puerto = int(getattr(settings, 'EMAIL_PORT', 0))
    except (TypeError, ValueError):
        puerto = 0
    try:
        timeout = int(getattr(settings, 'EMAIL_TIMEOUT', 0))
    except (TypeError, ValueError):
        timeout = 0

    if backend not in SMTP_BACKENDS:
        errores.append('EMAIL_BACKEND')
    if not host:
        errores.append('EMAIL_HOST')
    if not usuario:
        errores.append('EMAIL_HOST_USER')
    if not clave:
        errores.append('EMAIL_HOST_PASSWORD')
    if not remitente:
        errores.append('DEFAULT_FROM_EMAIL')
    if tls and ssl:
        errores.append('EMAIL_USE_TLS/EMAIL_USE_SSL')
    if ssl and puerto != 465:
        errores.append('EMAIL_PORT/EMAIL_USE_SSL')
    if tls and puerto not in {587, 2525}:
        errores.append('EMAIL_PORT/EMAIL_USE_TLS')
    if not tls and not ssl and puerto not in {25, 2525}:
        errores.append('EMAIL_PORT')
    if timeout < 1 or timeout > 120:
        errores.append('EMAIL_TIMEOUT')

    direccion_usuario = parseaddr(usuario)[1].lower()
    direccion_remitente = parseaddr(remitente)[1].lower()
    if (
        direccion_usuario
        and direccion_remitente
        and direccion_usuario != direccion_remitente
    ):
        errores.append('DEFAULT_FROM_EMAIL/EMAIL_HOST_USER')

    if errores:
        campos = ', '.join(dict.fromkeys(errores))
        raise ConfiguracionSMTPInvalida(
            f'La configuracion SMTP es incompleta o invalida: {campos}.'
        )


def construir_correo_captura_movil(
    *,
    recipient,
    continuation_url,
    expires_at,
    connection=None,
    es_prueba=False,
):
    recipient = normalizar_destinatario(recipient)
    contexto = {
        'brand_name': 'Aprobado',
        'continuation_url': continuation_url,
        'expires_at': expires_at,
        'es_prueba': es_prueba,
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/captura_movil.txt',
        contexto,
    )
    html = render_to_string(
        'emails/financiacion_educativa/captura_movil.html',
        contexto,
    )
    prefijo = '[PRUEBA] ' if es_prueba else ''
    mensaje = EmailMultiAlternatives(
        subject=f'{prefijo}{ASUNTO_CAPTURA_MOVIL}',
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


def construir_correo_correccion_automatica(
    *,
    recipient,
    requisitos,
    connection=None,
):
    recipient = normalizar_destinatario(recipient)
    requisitos_publicos = [str(item)[:80] for item in requisitos or []]
    titulo = 'Necesitamos una correccion en tu solicitud educativa'
    detalle = (
        'Debes repetir o actualizar los documentos indicados antes de continuar.'
    )
    if requisitos_publicos:
        detalle = f'{detalle} Requisitos: {", ".join(requisitos_publicos)}.'
    contexto = {
        'brand_name': 'Aprobado',
        'decision_type': TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
        'title': titulo,
        'message': detalle,
        'course_authorized': False,
        **_presentacion_decision(
            TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
        ),
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/decision_estado.txt', contexto
    )
    html = render_to_string(
        'emails/financiacion_educativa/decision_estado.html', contexto
    )
    mensaje = EmailMultiAlternatives(
        subject=titulo,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


def construir_correo_continuacion_automatica(*, recipient, connection=None):
    recipient = normalizar_destinatario(recipient)
    titulo = 'Tu expediente educativo esta listo para continuar a firma'
    contexto = {
        'brand_name': 'Aprobado',
        'decision_type': TipoDecisionRevisionEducativa.APPROVED,
        'title': titulo,
        'message': (
            'La validacion documental concluyo correctamente. Estamos '
            'preparando el pagare y la solicitud seguira pendiente hasta '
            'completar la firma.'
        ),
        'course_authorized': False,
        **_presentacion_decision(TipoDecisionRevisionEducativa.APPROVED),
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/decision_estado.txt', contexto
    )
    html = render_to_string(
        'emails/financiacion_educativa/decision_estado.html', contexto
    )
    mensaje = EmailMultiAlternatives(
        subject=titulo,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


def construir_correo_decision_educativa(
    *,
    recipient,
    decision,
    connection=None,
):
    recipient = normalizar_destinatario(recipient)
    titulos = {
        TipoDecisionRevisionEducativa.APPROVED: (
            'Tu expediente fue aprobado para continuar a firma'
        ),
        TipoDecisionRevisionEducativa.REJECTED: (
            'Resultado de tu solicitud educativa'
        ),
        TipoDecisionRevisionEducativa.CORRECTION_REQUESTED: (
            'Necesitamos una correccion en tu solicitud educativa'
        ),
    }
    contexto = {
        'brand_name': 'Aprobado',
        'decision_type': decision.tipo,
        'title': titulos[decision.tipo],
        'message': decision.mensaje_solicitante,
        'course_authorized': False,
        **_presentacion_decision(decision.tipo),
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/decision_estado.txt',
        contexto,
    )
    html = render_to_string(
        'emails/financiacion_educativa/decision_estado.html',
        contexto,
    )
    mensaje = EmailMultiAlternatives(
        subject=titulos[decision.tipo],
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


def construir_correo_expediente_recibido(
    *,
    recipient,
    referencia_externa,
    program_name='',
    course_name='',
    cc=None,
    connection=None,
):
    recipient = normalizar_destinatario(recipient)
    destinatarios_copia = []
    for correo in cc or []:
        normalizado = normalizar_destinatario(correo)
        if normalizado != recipient and normalizado not in destinatarios_copia:
            destinatarios_copia.append(normalizado)

    contexto = {
        'brand_name': 'Aprobado',
        'referencia_externa': str(referencia_externa or '').strip(),
        'program_name': str(program_name or '').strip(),
        'course_name': str(course_name or '').strip(),
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/expediente_recibido.txt',
        contexto,
    )
    html = render_to_string(
        'emails/financiacion_educativa/expediente_recibido.html',
        contexto,
    )
    mensaje = EmailMultiAlternatives(
        subject=ASUNTO_EXPEDIENTE_RECIBIDO,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        cc=destinatarios_copia,
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


def _url_detalle_operativo(solicitud):
    base = str(getattr(settings, 'BRAND_PUBLIC_BASE_URL', '')).rstrip('/')
    partes = urlsplit(base)
    if partes.scheme not in {'http', 'https'} or not partes.netloc:
        return ''
    if partes.scheme != 'https' and not settings.DEBUG:
        return ''
    ruta = reverse(
        'financiacion_educativa_web:operaciones:solicitud-detalle',
        kwargs={'application_id': solicitud.pk},
    )
    return f'{base}{ruta}'


def construir_correo_nueva_solicitud_interna(
    *,
    solicitud,
    recipients,
    connection=None,
):
    destinatarios = []
    for correo in recipients or []:
        normalizado = normalizar_destinatario(correo)
        if normalizado not in destinatarios:
            destinatarios.append(normalizado)
    if not destinatarios:
        raise ConfiguracionSMTPInvalida(
            'La notificacion interna requiere destinatarios validos.'
        )
    obtener_estado = getattr(solicitud, 'get_estado_display', None)
    estado = (
        obtener_estado()
        if callable(obtener_estado)
        else str(getattr(solicitud, 'estado', '') or 'Recibida')
    )
    contexto = {
        'brand_name': 'Aprobado',
        'reference': solicitud.referencia_externa,
        'program_name': solicitud.institucion.nombre_comercial,
        'course_name': solicitud.nombre_curso,
        'applicant_name': f'{solicitud.nombres} {solicitud.apellidos}'.strip(),
        'requested_amount': solicitud.valor_plan,
        'created_at': solicitud.creada_en,
        'status_label': estado,
        'masked_document': solicitud.identificacion_estudiante_enmascarada,
        'detail_url': _url_detalle_operativo(solicitud),
        'email_logo_url': obtener_email_logo_url(),
    }
    texto = render_to_string(
        'emails/financiacion_educativa/nueva_solicitud_interna.txt',
        contexto,
    )
    html = render_to_string(
        'emails/financiacion_educativa/nueva_solicitud_interna.html',
        contexto,
    )
    mensaje = EmailMultiAlternatives(
        subject=f'Nueva solicitud educativa | {solicitud.referencia_externa}',
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=destinatarios,
        connection=connection,
    )
    mensaje.attach_alternative(html, 'text/html')
    return mensaje


@dataclass(frozen=True)
class MuestraCorreoEducativo:
    codigo: str
    asunto: str
    titulo: str
    introduccion: str
    detalle: str
    boton: str
    nota: str = ''


MUESTRAS_CORREO_EDUCATIVO = (
    MuestraCorreoEducativo(
        codigo='recepcion-solicitud',
        asunto='Recibimos tu solicitud de financiación educativa',
        titulo='Tu solicitud fue recibida',
        introduccion=(
            'Registramos correctamente la solicitud educativa de demostración.'
        ),
        detalle=(
            'Referencia de muestra: EDU-DEMO-001. Te informaremos cuando haya '
            'un nuevo paso disponible.'
        ),
        boton='Consultar información',
    ),
    MuestraCorreoEducativo(
        codigo='documentos-pendientes',
        asunto='Completa los documentos de tu solicitud educativa',
        titulo='Aún tienes documentos pendientes',
        introduccion=(
            'Para continuar con la muestra del proceso, revisa los documentos '
            'indicados en tu expediente.'
        ),
        detalle=(
            'Esta comunicación usa información ficticia y no requiere cargar '
            'archivos reales.'
        ),
        boton='Revisar documentos',
    ),
    MuestraCorreoEducativo(
        codigo='enviada-revision',
        asunto='Tu solicitud educativa fue enviada a revisión',
        titulo='Iniciamos la revisión',
        introduccion=(
            'La documentación de muestra fue recibida y pasaría a validación '
            'manual.'
        ),
        detalle=(
            'No necesitas realizar ninguna acción mientras finaliza la '
            'revisión.'
        ),
        boton='Ver estado',
    ),
    MuestraCorreoEducativo(
        codigo='correcciones',
        asunto='Necesitamos una corrección en tu solicitud educativa',
        titulo='Hay información por corregir',
        introduccion=(
            'En una solicitud real, aquí se explicaría de forma clara el '
            'ajuste requerido.'
        ),
        detalle=(
            'Ejemplo ficticio: verificar la legibilidad de un documento antes '
            'de enviarlo nuevamente.'
        ),
        boton='Revisar correcciones',
    ),
    MuestraCorreoEducativo(
        codigo='aprobada',
        asunto='Tu solicitud educativa fue aprobada',
        titulo='Tu financiación fue aprobada',
        introduccion=(
            'Esta es una muestra visual de la comunicación de aprobación.'
        ),
        detalle=(
            'Los valores y condiciones contractuales solo se informarán desde '
            'el expediente real.'
        ),
        boton='Consultar siguientes pasos',
    ),
    MuestraCorreoEducativo(
        codigo='rechazada',
        asunto='Resultado de tu solicitud educativa',
        titulo='Finalizó la evaluación de tu solicitud',
        introduccion=(
            'En una comunicación real se presentaría el resultado con lenguaje '
            'claro y respetuoso.'
        ),
        detalle=(
            'Esta muestra no corresponde a una decisión ni a una persona real.'
        ),
        boton='Consultar información',
    ),
    MuestraCorreoEducativo(
        codigo='invitacion-firma-pagare',
        asunto='Invitación futura para firmar tu pagaré',
        titulo='Tu pagaré estaría listo para firma',
        introduccion=(
            'Esta plantilla muestra cómo se comunicaría una futura invitación '
            'de firma.'
        ),
        detalle=(
            'La función de firma no está habilitada y este botón no ejecuta '
            'ninguna acción.'
        ),
        boton='Revisar invitación',
        nota='Muestra de una funcionalidad futura.',
    ),
    MuestraCorreoEducativo(
        codigo='confirmacion-firma',
        asunto='Confirmación futura de firma del pagaré',
        titulo='La firma se confirmaría aquí',
        introduccion=(
            'Esta plantilla representa la confirmación posterior a una firma '
            'válida.'
        ),
        detalle=(
            'No se generó, envió ni firmó ningún documento para esta muestra.'
        ),
        boton='Consultar estado',
        nota='Muestra de una funcionalidad futura.',
    ),
)


def construir_correos_prueba(*, destinatario, connection=None):
    """Construye nueve muestras inertes sin consultar ni modificar la base."""
    mensajes = [
        construir_correo_captura_movil(
            recipient=destinatario,
            continuation_url=URL_MUESTRA_INERTE,
            expires_at=None,
            connection=connection,
            es_prueba=True,
        )
    ]
    for muestra in MUESTRAS_CORREO_EDUCATIVO:
        contexto = {
            'brand_name': 'Aprobado',
            'titulo': muestra.titulo,
            'introduccion': muestra.introduccion,
            'detalle': muestra.detalle,
            'boton': muestra.boton,
            'nota': muestra.nota,
            'action_url': URL_MUESTRA_INERTE,
        'email_logo_url': obtener_email_logo_url(),
        }
        texto = render_to_string(
            'emails/financiacion_educativa/muestra_estado.txt',
            contexto,
        )
        html = render_to_string(
            'emails/financiacion_educativa/muestra_estado.html',
            contexto,
        )
        mensaje = EmailMultiAlternatives(
            subject=f'[PRUEBA] {muestra.asunto}',
            body=texto,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario],
            connection=connection,
            headers={'X-Aprobado-Sample': muestra.codigo},
        )
        mensaje.attach_alternative(html, 'text/html')
        mensajes.append(mensaje)
    return mensajes
