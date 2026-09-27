from financiacion_educativa.choices import RequisitoCorreccionEducativa


TITULO_CORRECCION = 'Solo falta revisar un documento.'
CONTINUIDAD_CORRECCION = 'Tu solicitud sigue guardada y no necesitas comenzar nuevamente.'
CTA_CORRECCION = 'Revisar mi solicitud'
MENSAJES_RAZON = {
    'DATA_MISMATCH': (
        'No pudimos confirmar que el documento corresponda al titular de la solicitud. '
        'Verifica que muestre claramente el nombre completo o el número de identificación '
        'y vuelve a cargarlo.'
    ),
    'PDF_ENCRYPTED': (
        'El archivo está protegido con contraseña y no podemos revisarlo. Descarga una '
        'copia sin contraseña y vuelve a cargarla. Por seguridad, no nos envíes la contraseña.'
    ),
    'CATEGORY_MISMATCH': (
        'El archivo no corresponde a uno de los soportes financieros admitidos. Puedes '
        'cargar un certificado laboral, certificado de ingresos y retenciones, '
        'extracto bancario o certificación bancaria.'
    ),
}


def resolver_mensaje_correccion(requisito, razones=()):
    etiquetas = dict(RequisitoCorreccionEducativa.choices)
    etiqueta = etiquetas.get(requisito, 'Documento pendiente')
    for codigo in ('PDF_ENCRYPTED', 'DATA_MISMATCH', 'CATEGORY_MISMATCH'):
        if codigo in razones and (codigo != 'CATEGORY_MISMATCH' or requisito == 'INCOME_CERTIFICATE'):
            return f'{etiqueta}: {MENSAJES_RAZON[codigo]}'
    return f'{etiqueta}: revisa que esté completo y legible y vuelve a cargarlo.'


def razones_correccion_documental(solicitud):
    razones = {}
    for documento in solicitud.documentos.filter(activo=True, estado_validacion='REJECTED'):
        ultimo = documento.procesamientos_contenido.order_by('-numero').first()
        codigos = ultimo.codigos_razon if ultimo else [documento.motivo_rechazo]
        razones.setdefault(documento.tipo, []).extend(c for c in codigos if c in MENSAJES_RAZON)
    return razones


def mensaje_correccion_documento(documento):
    ultimo = documento.procesamientos_contenido.order_by('-numero').first()
    razones = ultimo.codigos_razon if ultimo else [documento.motivo_rechazo]
    return resolver_mensaje_correccion(documento.tipo, razones)
