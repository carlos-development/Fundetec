from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from financiacion_educativa.services.correos import obtener_email_logo_url
from financiacion_educativa.templatetags.recaudo_format import cop_recaudo


PRESENTACIONES_PAGO = {
    'BREB_REPORT_RECEIVED': {
        'titulo': 'Recibimos tu reporte',
        'mensaje': 'Recibimos tu reporte y est\u00e1 pendiente de revisi\u00f3n.',
        'etiqueta': 'Importe reportado',
        'nota': 'Este aviso no confirma el ingreso en el banco ni la aplicaci\u00f3n a una cuota.',
    },
    'BREB_INCOME_CONFIRMED': {
        'titulo': 'Ingreso confirmado en banco',
        'mensaje': 'Confirmamos el ingreso en el banco. Todav\u00eda no se ha aplicado a una cuota.',
        'etiqueta': 'Importe recibido',
        'nota': 'La confirmaci\u00f3n bancaria no equivale a una cuota pagada. No necesitas volver a transferir por esta espera.',
    },
    'BREB_PAYMENT_APPLIED': {
        'titulo': 'Pago aplicado a tu cuota',
        'mensaje': 'Aplicamos el pago a la cuota indicada.',
        'etiqueta': 'Importe aplicado',
        'nota': 'Esta confirmaci\u00f3n corresponde a la cuota indicada; no es una declaraci\u00f3n de deuda cancelada ni paz y salvo.',
    },
}


def construir_correo_pago(*, outbox, connection=None):
    reporte = outbox.reporte_recaudo
    codigo = outbox.codigo_mensaje
    ingreso = getattr(reporte, 'conciliacion', None)
    aplicacion = getattr(ingreso, 'aplicacion', None) if ingreso else None
    importe = (aplicacion.capital_aplicado + aplicacion.interes_aplicado
        if codigo == 'BREB_PAYMENT_APPLIED' else ingreso.monto_recibido
        if codigo == 'BREB_INCOME_CONFIRMED' else reporte.monto_reportado)
    contexto = {**PRESENTACIONES_PAGO[codigo], 'brand_name': 'Aprobado',
        'referencia': reporte.solicitud.referencia_externa, 'importe': cop_recaudo(importe),
        'cuota': aplicacion.cuota.numero if codigo == 'BREB_PAYMENT_APPLIED' else None,
        'email_logo_url': obtener_email_logo_url()}
    mensaje = EmailMultiAlternatives(subject=contexto['titulo'] + ' | Aprobado',
        body=render_to_string('emails/financiacion_educativa/pago_breb.txt', contexto),
        from_email=settings.DEFAULT_FROM_EMAIL, to=outbox.destinatarios, connection=connection)
    mensaje.attach_alternative(render_to_string('emails/financiacion_educativa/pago_breb.html', contexto), 'text/html')
    return mensaje
