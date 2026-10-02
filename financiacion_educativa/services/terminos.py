from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    EstadoVersionTerminos,
)
from financiacion_educativa.models import (
    Consentimiento,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.consentimientos import registrar_consentimiento
from financiacion_educativa.services.estados import transicionar_solicitud


@dataclass(frozen=True)
class ResultadoAceptacionTerminos:
    solicitud: SolicitudFinanciacionEducativa
    consentimientos: tuple
    repetida: bool


ESTADOS_REACEPTACION_TERMINOS = frozenset({
    EstadoSolicitudFinanciacion.PENDING_TERMS,
    EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
    EstadoSolicitudFinanciacion.PENDING_GUARDIAN,
    EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
    EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
    EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
})
CONTENIDOS_JURIDICOS_DE_PRUEBA = frozenset({
    'QA',
    'PRUEBA',
    'TEST',
    'TESTING',
    'TESTING QA',
})


def obtener_versiones_terminos_vigentes(*, obligatorios=True, ahora=None):
    ahora = ahora or timezone.now()
    consulta = VersionTerminosFinanciacion.objects.filter(
        estado=EstadoVersionTerminos.PUBLISHED,
        publicada_en__isnull=False,
        vigente_desde__lte=ahora,
        retirada_en__isnull=True,
    )
    if obligatorios is not None:
        consulta = consulta.filter(obligatorio=obligatorios)

    seleccionadas = {}
    for version in consulta.order_by('tipo', '-vigente_desde', '-publicada_en'):
        seleccionadas.setdefault(version.tipo, version)
    return list(seleccionadas.values())


def terminos_obligatorios_aceptados(*, solicitud):
    if not solicitud.usuario_id:
        return False
    vigentes = obtener_versiones_terminos_vigentes(obligatorios=True)
    if not vigentes:
        return False
    aceptados = set(
        Consentimiento.objects.filter(
            solicitud=solicitud,
            usuario_id=solicitud.usuario_id,
        ).values_list('tipo', 'version_texto')
    )
    return all(
        (version.tipo, version.version) in aceptados
        for version in vigentes
    )


def validar_versiones_juridicas_publicadas(*, versiones):
    for version in versiones:
        contenido = ' '.join(str(version.contenido or '').split()).upper()
        if (
            contenido in CONTENIDOS_JURIDICOS_DE_PRUEBA
            or '[CORREO Y DIRECCIÓN POR CONFIRMAR]' in contenido
        ):
            raise ValidationError(
                'Los textos juridicos vigentes aun no estan listos.',
                code='LEGAL_TERMS_NOT_READY',
            )


def validar_terminos_obligatorios_vigentes(*, solicitud):
    vigentes = obtener_versiones_terminos_vigentes(obligatorios=True)
    if not vigentes:
        raise ValidationError(
            'No hay terminos obligatorios vigentes.',
            code='LEGAL_TERMS_NOT_READY',
        )
    validar_versiones_juridicas_publicadas(versiones=vigentes)
    if not terminos_obligatorios_aceptados(solicitud=solicitud):
        raise ValidationError(
            'Debes revisar y aceptar los terminos vigentes antes de continuar.',
            code='CURRENT_TERMS_REQUIRED',
        )


def requiere_aceptacion_terminos_vigentes(*, solicitud):
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_TERMS:
        return True
    if solicitud.estado not in ESTADOS_REACEPTACION_TERMINOS:
        return False
    try:
        validar_terminos_obligatorios_vigentes(solicitud=solicitud)
    except ValidationError:
        return True
    return False


@transaction.atomic
def publicar_version_terminos(*, version, vigente_desde=None):
    version = VersionTerminosFinanciacion.objects.select_for_update().get(
        pk=version.pk
    )
    if version.estado != EstadoVersionTerminos.DRAFT:
        raise ValidationError({'estado': 'Solo puede publicarse un borrador.'})
    validar_versiones_juridicas_publicadas(versiones=[version])
    ahora = timezone.now()
    version.estado = EstadoVersionTerminos.PUBLISHED
    version.publicada_en = ahora
    version.vigente_desde = vigente_desde or ahora
    version.hash_integridad = version.calcular_hash(version.contenido)
    version.full_clean()
    version.save()
    return version


@transaction.atomic
def retirar_version_terminos(*, version):
    version = VersionTerminosFinanciacion.objects.select_for_update().get(
        pk=version.pk
    )
    if version.estado != EstadoVersionTerminos.PUBLISHED:
        raise ValidationError({'estado': 'Solo puede retirarse una version publicada.'})
    ahora = timezone.now()
    VersionTerminosFinanciacion.objects.filter(pk=version.pk).update(
        estado=EstadoVersionTerminos.RETIRED,
        retirada_en=ahora,
        actualizada_en=ahora,
    )
    version.estado = EstadoVersionTerminos.RETIRED
    version.retirada_en = ahora
    version.actualizada_en = ahora
    return version


@transaction.atomic
def aceptar_terminos_solicitud(
    *,
    solicitud,
    usuario,
    versiones,
    ip_address=None,
    user_agent='',
):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.usuario_id != usuario.pk:
        raise ValidationError('No es posible aceptar terminos para esta solicitud.')
    if solicitud.estado not in ESTADOS_REACEPTACION_TERMINOS:
        raise ValidationError({'estado': 'La solicitud no admite terminos.'})

    vigentes = obtener_versiones_terminos_vigentes(obligatorios=True)
    if not vigentes:
        raise ValidationError('No hay terminos obligatorios vigentes.')
    validar_versiones_juridicas_publicadas(versiones=vigentes)
    ids_vigentes = {version.pk for version in vigentes}
    ids_recibidos = {version.pk for version in versiones}
    if ids_recibidos != ids_vigentes:
        raise ValidationError(
            'Los terminos presentados cambiaron. Revise nuevamente su contenido.'
        )

    consentimientos = []
    creados = False
    for version in vigentes:
        consentimiento = Consentimiento.objects.filter(
            solicitud=solicitud,
            usuario=usuario,
            tipo=version.tipo,
            version_texto=version.version,
        ).first()
        if not consentimiento:
            consentimiento = registrar_consentimiento(
                solicitud=solicitud,
                usuario=usuario,
                tipo=version.tipo,
                version_texto=version.version,
                texto=version.contenido,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            creados = True
        consentimientos.append(consentimiento)

    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_TERMS:
        solicitud = transicionar_solicitud(
            solicitud=solicitud,
            nuevo_estado=EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
            actor=usuario,
            motivo='Terminos obligatorios vigentes aceptados.',
            metadata={
                'versions': sorted(version.version for version in vigentes),
            },
        )
    elif (
        creados
        and solicitud.estado
        == EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
    ):
        from financiacion_educativa.services.reaceptacion_contractual import (
            invalidar_paquete_pendiente_por_nuevos_terminos,
        )

        invalidar_paquete_pendiente_por_nuevos_terminos(
            solicitud=solicitud
        )
        solicitud_id = solicitud.pk

        def reanudar_automatizacion():
            from financiacion_educativa.services.orquestacion_automatica import (
                programar_orquestacion_automatica,
            )

            programar_orquestacion_automatica(solicitud_id=solicitud_id)

        transaction.on_commit(reanudar_automatizacion)
    return ResultadoAceptacionTerminos(
        solicitud=solicitud,
        consentimientos=tuple(consentimientos),
        repetida=not creados,
    )
