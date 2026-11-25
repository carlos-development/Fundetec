from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Exists, OuterRef, Q

from financiacion_educativa.models_reparto_intereses import ConvenioRepartoInteresEducativo
from financiacion_educativa.models_libro import MovimientoLibroEducativo


CERO = Decimal('0.00')
CENTAVO = Decimal('0.01')


def cargar_convenios(datos):
    consulta = ConvenioRepartoInteresEducativo.objects.filter(
        vigente_desde__lte=datos['hasta'], vigente_hasta__gte=datos['desde'],
    )
    if datos.get('institucion'):
        consulta = consulta.filter(institucion_id=datos['institucion'])
    por_institucion = defaultdict(list)
    for convenio in consulta:
        por_institucion[convenio.institucion_id].append(convenio)
    return por_institucion


def _convenio_aplicable(convenios, institucion_id, fecha):
    vigentes = [convenio for convenio in convenios.get(institucion_id, ())
                if convenio.vigente_desde <= fecha <= convenio.vigente_hasta]
    return vigentes[0] if len(vigentes) == 1 else None


def _parte_aprobado(importe, convenio):
    return (importe * convenio.porcentaje_aprobado / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _partes(importe, aprobado):
    return {'aprobado': aprobado, 'institucion': importe - aprobado}


def tiene_aplicacion_registrada():
    return Q(aplicacion__isnull=False) | Exists(MovimientoLibroEducativo.objects.filter(
        ingreso_id=OuterRef('pk'),
    ).filter(Q(capital__gt=0) | Q(intereses__gt=0)))


def repartir_cuotas(ingreso, convenios):
    convenio = _convenio_aplicable(convenios, ingreso.reporte.solicitud.institucion_id, ingreso.fecha_efectiva)
    cuotas = ingreso.cuotas_aplicadas
    neto = ingreso.interes_neto.quantize(CENTAVO)
    ingreso.mostrar_reparto_sin_cuota = not cuotas and bool(neto)
    ingreso.reparto_sin_cuota = None
    if convenio is None:
        return

    # Round once per bank movement; cumulative rounding distributes that cent total across its rows.
    acumulado = aprobado_anterior = CERO
    for cuota in cuotas:
        interes = cuota['interes_neto']
        acumulado += interes
        aprobado = _parte_aprobado(acumulado, convenio)
        cuota['reparto'] = _partes(interes, aprobado - aprobado_anterior)
        aprobado_anterior = aprobado

    diferencia = neto - acumulado
    if diferencia or (not cuotas and ingreso.tiene_aplicacion_registrada):
        ingreso.mostrar_reparto_sin_cuota = True
        ingreso.reparto_sin_cuota = _partes(diferencia, _parte_aprobado(neto, convenio) - aprobado_anterior)


def acumular_reparto(mes, convenios, institucion_id, fecha, interes):
    convenio = _convenio_aplicable(convenios, institucion_id, fecha)
    if convenio is None:
        mes['interes_sin_convenio'] += interes
        return
    if mes['reparto'] is None:
        mes['reparto'] = _partes(CERO, CERO)
    aprobado = _parte_aprobado(interes, convenio)
    mes['reparto']['aprobado'] += aprobado
    mes['reparto']['institucion'] += interes - aprobado
