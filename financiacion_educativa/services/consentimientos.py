import hashlib

from django.core.exceptions import ValidationError

from financiacion_educativa.models import Consentimiento


def calcular_evidencia_consentimiento(*, tipo, version_texto, texto):
    if not version_texto or not texto:
        raise ValidationError('La version y el texto del consentimiento son obligatorios.')
    contenido = f'{tipo}\n{version_texto}\n{texto}'.encode('utf-8')
    return hashlib.sha256(contenido).hexdigest()


def registrar_consentimiento(
    *,
    solicitud,
    tipo,
    version_texto,
    texto,
    participante=None,
    usuario=None,
    ip_address=None,
    user_agent='',
):
    consentimiento = Consentimiento(
        solicitud=solicitud,
        participante=participante,
        usuario=usuario,
        tipo=tipo,
        version_texto=version_texto,
        ip_address=ip_address,
        user_agent=(user_agent or '')[:512],
        evidencia_hash=calcular_evidencia_consentimiento(
            tipo=tipo,
            version_texto=version_texto,
            texto=texto,
        ),
    )
    consentimiento.full_clean()
    consentimiento.save()
    return consentimiento
