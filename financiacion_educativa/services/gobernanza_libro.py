"""Serialised approval and pilot gates. Bank date selects policy, never posting date."""
import hashlib
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from instituciones.models import Institucion
from financiacion_educativa.models_libro import PoliticaLibroEducativo
from financiacion_educativa.models_gobernanza_libro import (
    PropuestaPoliticaLibro, EventoPoliticaLibro, EventoAlcanceLibro,
    ControlPilotoLibro, EventoControlPilotoLibro,
)
from financiacion_educativa.dashboards.operaciones.permissions import PERMISO_ACCESO
from .recaudos import PERMISO_CONSULTAR, exigir_permiso


PERMISOS = {
    'PREPARE': 'financiacion_educativa.preparar_politicas_libro',
    'REVIEW': 'financiacion_educativa.revisar_politicas_libro',
    'APPROVE': 'financiacion_educativa.aprobar_politicas_libro',
    'POST': 'financiacion_educativa.aplicar_cuotas_educativas',
    'COMPENSATE': 'financiacion_educativa.compensar_libro_educativo',
    'CONTROL': 'financiacion_educativa.controlar_piloto_libro',
}
PERMISO_ALCANCES = 'financiacion_educativa.gestionar_alcances_libro'


class ErrorGobernanzaLibro(ValidationError):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje, code=codigo)


def _hash(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, default=str).encode()).hexdigest()


def _texto(texto):
    if not isinstance(texto, str) or not texto.strip() or len(texto) > 1000:
        raise ValidationError('Motivo o respaldo obligatorio, hasta 1000 caracteres.')
    return texto.strip()


def actor_actual(actor):
    if actor is None or not actor.pk:
        raise PermissionDenied
    actual = get_user_model().objects.get(pk=actor.pk)
    exigir_permiso(actual, PERMISO_ACCESO)
    return actual


def exigir_alcance(actor, institucion_id, accion):
    actual = actor_actual(actor)
    exigir_permiso(actual, PERMISOS[accion])
    if accion in ('POST', 'COMPENSATE'):
        exigir_permiso(actual, PERMISO_CONSULTAR)
    alcance = EventoAlcanceLibro.objects.filter(usuario=actual, institucion_id=institucion_id,
        accion=accion).order_by('-pk').first()
    if not alcance or not alcance.habilitado:
        raise PermissionDenied('No tiene alcance explicito para esta institucion y accion del libro.')
    return alcance


def puede_consultar_politicas(actor, institucion_id):
    actual = actor_actual(actor)
    if actual.has_perm(PERMISO_ALCANCES):
        return True
    for accion, permiso in PERMISOS.items():
        if actual.has_perm(permiso):
            ultimo = EventoAlcanceLibro.objects.filter(usuario=actual, institucion_id=institucion_id,
                accion=accion).order_by('-pk').first()
            if ultimo and ultimo.habilitado:
                return True
    raise PermissionDenied('No tiene alcance para consultar politicas de esta institucion.')


@transaction.atomic
def asignar_alcance(*, institucion_id, usuario_id, accion, habilitado, actor, motivo):
    actual = actor_actual(actor)
    exigir_permiso(actual, PERMISO_ALCANCES)
    Institucion.objects.select_for_update().get(pk=institucion_id)
    if accion not in PERMISOS or type(habilitado) is not bool:
        raise ValidationError('Accion o habilitacion invalida.')
    get_user_model().objects.get(pk=usuario_id, is_active=True)
    motivo = _texto(motivo)
    ultimo = EventoAlcanceLibro.objects.filter(institucion_id=institucion_id,
        usuario_id=usuario_id, accion=accion).order_by('-pk').first()
    if ultimo and (ultimo.habilitado, ultimo.actor_id, ultimo.motivo) == (habilitado, actual.pk, motivo):
        return ultimo
    return EventoAlcanceLibro.objects.create(institucion_id=institucion_id, usuario_id=usuario_id,
        accion=accion, habilitado=habilitado, actor=actual, motivo=motivo)


@transaction.atomic
def preparar_politica(*, institucion_id, version, reglas, vigente_desde, vigente_hasta,
                     es_sintetica, respaldo, actor):
    Institucion.objects.select_for_update().get(pk=institucion_id)
    exigir_alcance(actor, institucion_id, 'PREPARE')
    if type(es_sintetica) is not bool:
        raise ValidationError('Clasifique explicitamente la politica.')
    if es_sintetica and settings.DEPLOYMENT_ENVIRONMENT not in ('staging', 'test'):
        raise ErrorGobernanzaLibro('SYNTHETIC_FORBIDDEN', 'Las politicas sinteticas solo se permiten en staging o test.')
    datos = dict(reglas=reglas, vigente_desde=vigente_desde, vigente_hasta=vigente_hasta,
        es_sintetica=es_sintetica, respaldo=_texto(respaldo), preparada_por_id=actor.pk)
    anterior = PropuestaPoliticaLibro.objects.filter(institucion_id=institucion_id, version=version).first()
    if anterior:
        if any(getattr(anterior, k) != v for k, v in datos.items()):
            raise ValidationError('La version ya existe con otros datos; prepare una version nueva.')
        return anterior
    return PropuestaPoliticaLibro.objects.create(institucion_id=institucion_id, version=version, **datos)


def _evento(propuesta, tipo, actor, respaldo, **detalle):
    respaldo = _texto(respaldo)
    huella = _hash({'propuesta': propuesta.pk, 'tipo': tipo, 'actor': actor.pk,
        'respaldo': respaldo, **detalle})
    anterior = propuesta.eventos.filter(tipo=tipo).first()
    if anterior:
        if anterior.payload_sha256 != huella:
            raise ValidationError('El evento ya existe con otra autoria o contenido.')
        return anterior
    return EventoPoliticaLibro.objects.create(propuesta=propuesta, tipo=tipo, actor=actor,
        respaldo=respaldo, payload_sha256=huella)


@transaction.atomic
def revisar_politica(*, propuesta_id, actor, respaldo, aceptar=True):
    propuesta = PropuestaPoliticaLibro.objects.get(pk=propuesta_id)
    Institucion.objects.select_for_update().get(pk=propuesta.institucion_id)
    exigir_alcance(actor, propuesta.institucion_id, 'REVIEW')
    if actor.pk == propuesta.preparada_por_id:
        raise PermissionDenied('La revision debe ser independiente de la preparacion.')
    if propuesta.eventos.filter(tipo__in=['APPROVED', 'RETIRED']).exists():
        raise ValidationError('La politica aprobada no se reescribe; prepare otra version.')
    opuesto = 'REJECTED' if aceptar else 'REVIEWED'
    if type(aceptar) is not bool or propuesta.eventos.filter(tipo=opuesto).exists():
        raise ValidationError('La revision ya esta resuelta; prepare otra version.')
    return _evento(propuesta, 'REVIEWED' if aceptar else 'REJECTED', actor, respaldo)


def politicas_vigentes(institucion_id, fecha):
    return PoliticaLibroEducativo.objects.filter(institucion_id=institucion_id,
        propuesta__isnull=False, propuesta__eventos__tipo='APPROVED', vigente_desde__lte=fecha).filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=fecha)
    ).exclude(propuesta__eventos__tipo='RETIRED')


def resolver_politica(institucion_id, fecha, politica_id=None):
    candidatas = list(politicas_vigentes(institucion_id, fecha))
    if len(candidatas) != 1:
        raise ErrorGobernanzaLibro('POLICY_MISSING_OR_AMBIGUOUS', 'No hay una unica politica aprobada vigente para la fecha bancaria.')
    politica = candidatas[0]
    if politica_id and politica.pk != politica_id:
        raise ErrorGobernanzaLibro('POLICY_CHANGED', 'La politica cambio o no aplica a esta fecha bancaria. Recarga la vista previa.')
    if settings.DEPLOYMENT_ENVIRONMENT not in ('staging', 'test', 'production'):
        raise ErrorGobernanzaLibro('ENVIRONMENT_UNSUPPORTED', 'Ambiente no autorizado para el libro.')
    if politica.es_sintetica is not False and settings.DEPLOYMENT_ENVIRONMENT == 'production':
        raise ErrorGobernanzaLibro('SYNTHETIC_FORBIDDEN', 'Produccion no admite politicas sinteticas ni de clasificacion historica desconocida.')
    from .libro_pagos import validar_reglas
    validar_reglas(politica.reglas)
    propuesta = politica.propuesta
    campos = ('institucion_id', 'version', 'reglas', 'vigente_desde', 'vigente_hasta', 'es_sintetica')
    if any(getattr(politica, c) != getattr(propuesta, c) for c in campos):
        raise ErrorGobernanzaLibro('POLICY_INCONSISTENT', 'La politica no corresponde a su propuesta aprobada.')
    return politica


@transaction.atomic
def aprobar_politica(*, propuesta_id, actor, respaldo, reemplaza_id=None, aceptar_uso_real=False):
    propuesta = PropuestaPoliticaLibro.objects.get(pk=propuesta_id)
    Institucion.objects.select_for_update().get(pk=propuesta.institucion_id)
    exigir_alcance(actor, propuesta.institucion_id, 'APPROVE')
    if actor.pk == propuesta.preparada_por_id:
        raise PermissionDenied('La aprobacion debe ser independiente de la preparacion.')
    from .libro_pagos import validar_reglas
    validar_reglas(propuesta.reglas)
    if not propuesta.es_sintetica and aceptar_uso_real is not True:
        raise ValidationError('La aprobacion no sintetica exige confirmar el respaldo comercial y el uso real.')
    if propuesta.es_sintetica and settings.DEPLOYMENT_ENVIRONMENT not in ('staging', 'test'):
        raise ErrorGobernanzaLibro('SYNTHETIC_FORBIDDEN', 'No se aprueban politicas sinteticas en produccion.')
    if propuesta.eventos.filter(tipo__in=['REJECTED', 'RETIRED']).exists() or not propuesta.eventos.filter(tipo='REVIEWED').exists():
        raise ValidationError('Se requiere revision favorable previa y una propuesta no retirada.')
    existente = getattr(propuesta, 'politica_aprobada', None)
    if existente:
        _evento(propuesta, 'APPROVED', actor, respaldo, reemplaza=reemplaza_id, uso_real=aceptar_uso_real)
        return existente
    activas = PoliticaLibroEducativo.objects.filter(institucion_id=propuesta.institucion_id,
        propuesta__eventos__tipo='APPROVED').exclude(propuesta__eventos__tipo='RETIRED')
    if reemplaza_id:
        anterior = activas.filter(pk=reemplaza_id).first()
        if not anterior:
            raise ValidationError('La version a reemplazar ya no esta activa. Recarga la propuesta.')
        if (anterior.vigente_desde, anterior.vigente_hasta) != (propuesta.vigente_desde, propuesta.vigente_hasta):
            raise ValidationError('Un reemplazo debe conservar el intervalo completo para no crear vacios implicitos.')
        _evento(anterior.propuesta, 'RETIRED', actor, respaldo, reemplazada_por=propuesta.pk)
        activas = activas.exclude(pk=anterior.pk)
    solapadas = activas.filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=propuesta.vigente_desde))
    if propuesta.vigente_hasta:
        solapadas = solapadas.filter(vigente_desde__lte=propuesta.vigente_hasta)
    if solapadas.exists():
        raise ErrorGobernanzaLibro('POLICY_OVERLAP', 'El intervalo se solapa con una politica aprobada; no pueden aplicarse dos versiones.')
    politica = PoliticaLibroEducativo.objects.create(institucion=propuesta.institucion,
        version=propuesta.version, reglas=propuesta.reglas, vigente_desde=propuesta.vigente_desde,
        vigente_hasta=propuesta.vigente_hasta, es_sintetica=propuesta.es_sintetica,
        propuesta=propuesta, referencia_aprobacion=_texto(respaldo)[:200], aprobada_por=actor)
    _evento(propuesta, 'APPROVED', actor, respaldo, reemplaza=reemplaza_id, uso_real=aceptar_uso_real)
    return politica


@transaction.atomic
def retirar_politica(*, politica_id, actor, respaldo):
    politica = PoliticaLibroEducativo.objects.get(pk=politica_id, propuesta__isnull=False)
    Institucion.objects.select_for_update().get(pk=politica.institucion_id)
    exigir_alcance(actor, politica.institucion_id, 'APPROVE')
    return _evento(politica.propuesta, 'RETIRED', actor, respaldo)


def bloquear_piloto(institucion_id):
    control = ControlPilotoLibro.objects.select_for_update().get(pk=1)
    Institucion.objects.select_for_update().get(pk=institucion_id)
    return control


def exigir_contabilizacion(*, actor, institucion_id, fecha, politica_id=None, accion='POST', control=None):
    alcance = exigir_alcance(actor, institucion_id, accion)
    if getattr(settings, 'FINANCIACION_EDUCATIVA_LIBRO_ENABLED', False) is not True:
        raise ErrorGobernanzaLibro('FLAG_DISABLED', 'Contabilizacion 2B bloqueada: el flag esta desactivado.')
    control = control or ControlPilotoLibro.objects.get(pk=1)
    if not control.activo or control.institucion_id != institucion_id:
        raise ErrorGobernanzaLibro('PILOT_CLOSED', 'Contabilizacion 2B bloqueada: la institucion no tiene un piloto activo.')
    politica = resolver_politica(institucion_id, fecha, politica_id)
    return politica, control.revision, alcance.pk


@transaction.atomic
def controlar_piloto(*, institucion_id, activo, actor, motivo):
    control = bloquear_piloto(institucion_id)
    exigir_alcance(actor, institucion_id, 'CONTROL')
    motivo = _texto(motivo)
    if type(activo) is not bool:
        raise ValidationError('Estado de piloto invalido.')
    if control.activo and control.institucion_id != institucion_id:
        raise ValidationError('Debe detener el piloto de la otra institucion antes de cambiar de alcance.')
    if activo:
        resolver_politica(institucion_id, timezone.localdate())
    if control.institucion_id == institucion_id and control.activo == activo:
        return control
    control.activo, control.institucion_id, control.revision = activo, institucion_id, control.revision + 1
    control._servicio_control = True
    control.save()
    EventoControlPilotoLibro.objects.create(institucion_id=institucion_id, revision=control.revision,
        activo=activo, actor=actor, motivo=motivo)
    return control
