"""Synthetic governance through the same services as Operations, never production fixtures."""
from financiacion_educativa.models_gobernanza_libro import ControlPilotoLibro
from financiacion_educativa.services import gobernanza_libro as gobierno
from financiacion_educativa.services.libro_pagos import REGLAS_SINTETICAS


def conceder_alcances(institucion, usuario, administrador, acciones=None):
    for accion in acciones or gobierno.PERMISOS:
        gobierno.asignar_alcance(institucion_id=institucion.pk, usuario_id=usuario.pk, accion=accion,
            habilitado=True, actor=administrador, motivo='Alcance sintetico exclusivo de prueba')


def politica_sintetica(institucion, preparador, aprobador, version, desde, reglas=None, hasta=None, reemplaza_id=None):
    conceder_alcances(institucion, preparador, preparador)
    conceder_alcances(institucion, aprobador, preparador)
    propuesta = gobierno.preparar_politica(institucion_id=institucion.pk, version=version,
        reglas=reglas or dict(REGLAS_SINTETICAS), vigente_desde=desde, vigente_hasta=hasta,
        es_sintetica=True, respaldo='SYNTHETIC_ONLY_NOT_COMMERCIAL_APPROVAL', actor=preparador)
    gobierno.revisar_politica(propuesta_id=propuesta.pk, actor=aprobador, respaldo='Revision sintetica de prueba')
    return gobierno.aprobar_politica(propuesta_id=propuesta.pk, actor=aprobador,
        respaldo='Aprobacion exclusivamente sintetica', reemplaza_id=reemplaza_id)


def preparar_control_prueba():
    # TransactionTestCase.flush removes migration seed data between test cases.
    if not ControlPilotoLibro.objects.filter(pk=1).exists():
        control = ControlPilotoLibro(pk=1)
        control._servicio_control = True
        control.save(force_insert=True)
