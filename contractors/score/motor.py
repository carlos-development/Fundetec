from decimal import Decimal, ROUND_HALF_UP

from contractors.score.configuracion import CONFIGURACION_SCORE_PRESTADORES_V1
from contractors.score.dto import (
    ComponenteScorePrestador,
    EntradaScoreInternoPrestador,
    PenalizacionScorePrestador,
    ResultadoScoreInternoPrestador,
)
from contractors.score.policies import (
    decimal_configuracion,
    normalizar_puntaje,
    resolver_banda,
    validar_configuracion_score,
)


ESTADO_EVALUADO = 'EVALUADO'
ESTADO_PENDIENTE = 'PENDIENTE'


def evaluar_score_interno_prestador(
    entrada: EntradaScoreInternoPrestador,
    configuracion=None,
) -> ResultadoScoreInternoPrestador:
    configuracion = configuracion or CONFIGURACION_SCORE_PRESTADORES_V1
    validar_configuracion_score(configuracion)

    componentes = []
    componentes_pendientes = []
    penalizaciones = []
    suma_ponderada = Decimal('0.00')
    suma_pesos_evaluados = Decimal('0.00')

    for nombre, datos_componente in configuracion.get('componentes', {}).items():
        if datos_componente.get('penaliza'):
            penalizacion = _evaluar_penalizacion(nombre, datos_componente, entrada)
            if penalizacion:
                penalizaciones.append(penalizacion)
            continue

        componente = _evaluar_componente(nombre, datos_componente, entrada)
        componentes.append(componente)

        if componente.estado == ESTADO_PENDIENTE:
            componentes_pendientes.append(nombre)
            continue

        suma_ponderada += componente.puntaje_ponderado
        suma_pesos_evaluados += componente.peso

    if suma_pesos_evaluados > Decimal('0.00'):
        score_base = suma_ponderada / suma_pesos_evaluados
    else:
        score_base = Decimal('0.00')

    score_con_penalizaciones = score_base + sum(
        (penalizacion.penalizacion for penalizacion in penalizaciones),
        Decimal('0.00'),
    )
    score_final = normalizar_puntaje(score_con_penalizaciones)
    banda = resolver_banda(score_final, configuracion)
    razones = _razones_resultado(componentes_pendientes, penalizaciones)
    requiere_revision_manual = bool(componentes_pendientes) or banda.nombre == 'REVISION'

    return ResultadoScoreInternoPrestador(
        version_configuracion=configuracion['version'],
        score_final=score_final,
        banda=banda,
        decision_preliminar=banda.decision,
        monto_maximo_sugerido=banda.monto_maximo,
        plazo_maximo_sugerido=banda.plazo_maximo_meses,
        componentes=tuple(componentes),
        componentes_pendientes=tuple(componentes_pendientes),
        penalizaciones=tuple(penalizaciones),
        razones=tuple(razones),
        requiere_revision_manual=requiere_revision_manual,
        datacredito_status=entrada.datacredito_status,
    )


def _evaluar_componente(nombre, datos_componente, entrada):
    peso = decimal_configuracion(datos_componente.get('peso'))
    valor = entrada.componentes.get(nombre, datos_componente.get('valor_default'))
    if valor is None:
        return ComponenteScorePrestador(
            nombre=nombre,
            peso=peso,
            valor=None,
            estado=datos_componente.get('estado_si_no_disponible', ESTADO_PENDIENTE),
            razon=f'{nombre}_pendiente',
        )

    valor = normalizar_puntaje(valor)
    return ComponenteScorePrestador(
        nombre=nombre,
        peso=peso,
        valor=valor,
        puntaje_ponderado=(valor * peso).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        estado=ESTADO_EVALUADO,
    )


def _evaluar_penalizacion(nombre, datos_componente, entrada):
    valor = entrada.componentes.get(nombre, datos_componente.get('valor_default'))
    if valor is None:
        return None

    valor = normalizar_puntaje(valor)
    umbral = decimal_configuracion(datos_componente.get('umbral_penalizacion'))
    if valor >= umbral:
        return None

    penalizacion = decimal_configuracion(datos_componente.get('penalizacion'))
    return PenalizacionScorePrestador(
        nombre=nombre,
        valor=valor,
        penalizacion=penalizacion,
        razon=f'{nombre}_bajo_umbral',
    )


def _razones_resultado(componentes_pendientes, penalizaciones):
    razones = []
    razones.extend(f'componente_pendiente:{nombre}' for nombre in componentes_pendientes)
    razones.extend(penalizacion.razon for penalizacion in penalizaciones)
    return razones
