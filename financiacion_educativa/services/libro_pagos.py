"""Versioned previews and append-only allocations; no approved production defaults."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, DecimalField, F, JSONField, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.models_libro import LineaLibroEducativo, MovimientoLibroEducativo
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ArchivoRecaudoEducativo, ConciliacionRecaudoEducativo
from .aplicacion_cuotas import exigir_operador
from .motor_financiero import calcular_interes_causado_diario_30, generar_plan_reduccion_plazo, sumar_meses
from .recaudos import PERMISO_CONSULTAR, exigir_permiso, habilitados, leer_soporte_seguro
from . import gobernanza_libro as gobierno


CERO = Decimal('0.00')
SALT = 'educational-ledger-preview-v1'
MAX_AGE = 900
MODOS = (('FULL', 'Cuota completa'), ('PARTIAL', 'Pago parcial'), ('PRINCIPAL', 'Abono a capital'), ('PAYOFF', 'Pago total'))
# These are named simulation scenarios, never a fallback policy for posting.
REGLAS_SINTETICAS = {
    'imputacion': 'INTEREST_FIRST', 'vencidos': 'OLDEST_FIRST',
    'excedente': 'UNALLOCATED', 'retroactividad': 'REJECT_BEFORE_LAST',
    'centavos': 'EXACT_BANK_CONTRACT_HALF_UP', 'compensacion': 'LAST_FULL_ONLY',
    'causacion': 'SAVED_PERIODS_DAILY_30', 'abono_vencido': 'BLOCK',
    'intereses_anticipados': 'BLOCK_REPROJECTION',
}


def validar_reglas(reglas):
    if not isinstance(reglas, dict) or set(reglas) != set(REGLAS_SINTETICAS):
        raise ValidationError('La politica debe definir todas las decisiones del libro.')
    for campo, valor in reglas.items():
        permitidos = ('INTEREST_FIRST', 'CAPITAL_FIRST') if campo == 'imputacion' else (REGLAS_SINTETICAS[campo],)
        if valor not in permitidos:
            raise ValidationError('Alternativa de politica no implementada: ' + campo)


def _hash(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _dinero(valor):
    valor = Decimal(str(valor))
    if not valor.is_finite() or valor < 0 or valor != valor.quantize(Decimal('0.01')):
        raise ValidationError('Importe no valido; no se redondea el dinero bancario.')
    return valor


def _signo():
    return Case(When(tipo='COMPENSATION', then=Value(-1)), default=Value(1))


def anotar_ingresos(consulta):
    """Independent subqueries prevent multiplying money by journal line joins."""
    nuevos = MovimientoLibroEducativo.objects.filter(ingreso_id=OuterRef('pk')).order_by()
    for nombre, campo in (('capital_libro', 'capital'), ('interes_libro', 'intereses')):
        total = nuevos.values('ingreso_id').annotate(suma=Sum(F(campo) * _signo())).values('suma')
        consulta = consulta.annotate(**{nombre: Coalesce(Subquery(total, output_field=DecimalField(max_digits=18, decimal_places=2)), CERO)})
    return consulta.annotate(
        capital_neto=Coalesce('aplicacion__capital_aplicado', CERO) + F('capital_libro'),
        interes_neto=Coalesce('aplicacion__interes_aplicado', CERO) + F('interes_libro'),
    ).annotate(aplicado_neto=F('capital_neto') + F('interes_neto')).annotate(
        disponible_libro=F('monto_recibido') - F('aplicado_neto'))


def resumen_ingresos(consulta):
    return anotar_ingresos(consulta).aggregate(
        recibido=Sum('monto_recibido', default=CERO), capital=Sum('capital_neto', default=CERO),
        intereses=Sum('interes_neto', default=CERO), sin_destino=Sum('disponible_libro', default=CERO),
    )


def anotar_reportes(consulta):
    ingreso = anotar_ingresos(ConciliacionRecaudoEducativo.objects.filter(reporte_id=OuterRef('pk')))
    return consulta.annotate(
        dinero_aplicado=Coalesce(Subquery(ingreso.values('aplicado_neto')[:1]), CERO),
        dinero_sin_destino=Coalesce(Subquery(ingreso.values('disponible_libro')[:1]), CERO))


def anotar_cuotas(consulta):
    lineas = LineaLibroEducativo.objects.filter(cuota_id=OuterRef('pk')).order_by()
    signo = Case(When(movimiento__tipo='COMPENSATION', then=Value(-1)), default=Value(1))
    for nombre, concepto, historico in (('capital_cubierto', 'CAPITAL', 'capital_aplicado'),
                                      ('interes_cubierto', 'INTEREST', 'interes_aplicado')):
        suma = lineas.filter(concepto=concepto).values('cuota_id').annotate(total=Sum(F('importe') * signo)).values('total')
        consulta = consulta.annotate(**{nombre: Coalesce('aplicacion_recaudo__' + historico, CERO) +
            Coalesce(Subquery(suma, output_field=DecimalField(max_digits=18, decimal_places=2)), CERO)})
    return consulta.annotate(cubierta_libro=Case(When(capital_cubierto__gte=F('capital'),
        interes_cubierto__gte=F('interes'), then=Value(True)), default=Value(False)))


def anotar_estado_actual(consulta, *, fotografia_ref='pk'):
    ultimo = MovimientoLibroEducativo.objects.filter(fotografia_id=OuterRef(fotografia_ref)).order_by('-secuencia')
    return consulta.annotate(estado_actual_libro=Subquery(ultimo.values('estado_despues')[:1], output_field=JSONField()))


def estado_libro(foto):
    historicas = list(AplicacionCuotaEducativa.objects.filter(cuota__fotografia=foto).order_by('cuota__numero').values(
        'pk', 'cuota_id', 'capital_aplicado', 'interes_aplicado', 'fecha_efectiva'))
    huella = _hash([{k: str(v) for k, v in a.items()} for a in historicas])
    ultimo = MovimientoLibroEducativo.objects.filter(fotografia=foto).order_by('-secuencia').first()
    if ultimo:
        estado = json.loads(json.dumps(ultimo.estado_despues))
        if estado['historico'] != huella:
            raise ValidationError('Hay una aplicacion integra posterior al libro. Requiere conciliacion operativa; no se reprocesa automaticamente.')
        estado['secuencia'] = ultimo.secuencia
        return estado
    cubiertas = {str(a['cuota_id']) for a in historicas}
    cuotas = list(foto.cuotas.order_by('numero'))
    plan = [{'cuota': str(c.pk), 'numero': c.numero, 'fecha': c.fecha_vencimiento.isoformat(),
             'capital': str(c.capital), 'interes': str(c.interes), 'interes_original': str(c.interes),
             'corte': (cuotas[i - 1].fecha_vencimiento if i else foto.fecha_inicio_plan).isoformat()}
            for i, c in enumerate(cuotas) if str(c.pk) not in cubiertas]
    capital = foto.capital_financiado - sum((a['capital_aplicado'] for a in historicas), CERO)
    if capital < 0 or sum((_dinero(c['capital']) for c in plan), CERO) != capital:
        raise ValidationError('El calendario y las aplicaciones historicas no cuadran.')
    return {'motor': 'EDU_LEDGER_V1', 'historico': huella, 'secuencia': 0, 'plan': plan, 'capital': str(capital),
            'ultima_fecha': max((a['fecha_efectiva'] for a in historicas), default=foto.fecha_inicio_plan).isoformat(),
            'proyectado': False}


def _validar_ingreso(ingreso):
    reporte = ingreso.reporte
    if not obligaciones_autorizadas().filter(pk=reporte.fotografia_id, solicitud_id=reporte.solicitud_id).exists():
        raise ValidationError('Se requiere la version contractual efectivamente autorizada.')
    if hasattr(reporte, 'rechazo') or ingreso.fecha_efectiva > timezone.localdate():
        raise ValidationError('Ingreso rechazado o fecha bancaria futura.')
    if ingreso.monto_recibido != reporte.monto_reportado:
        raise ValidationError('La diferencia con el reporte exige revision previa.')
    evidencia = ingreso.evidencia_movimiento
    if evidencia.finalidad != 'BANK_EVIDENCE' or evidencia.creado_por_id != ingreso.actor_id:
        raise ValidationError('La evidencia bancaria no corresponde al ingreso.')
    leer_soporte_seguro(evidencia)
    if reporte.origen == 'PAYER':
        if ingreso.cuenta_id != reporte.receptor.cuenta_id or reporte.comprobante.finalidad != 'RECEIPT' or reporte.comprobante.creado_por_id != reporte.reportado_por_id:
            raise ValidationError('El soporte o cuenta no corresponde al pagador.')
        leer_soporte_seguro(reporte.comprobante)


def _politica(ingreso, politica_id):
    if politica_id is None:
        return None, dict(REGLAS_SINTETICAS), 'SIMULACION_NO_APROBADA'
    politica = gobierno.resolver_politica(ingreso.reporte.solicitud.institucion_id,
        ingreso.fecha_efectiva, politica_id)
    validar_reglas(politica.reglas)
    return politica, politica.reglas, politica.version


def calcular_distribucion(*, foto, estado, recibido, fecha, modo, reglas):
    """Pure Decimal engine over remaining saved schedule, not operator-paid counts."""
    validar_reglas(reglas)
    if modo not in dict(MODOS):
        raise ValidationError('Tipo de aplicacion no permitido.')
    if fecha < date.fromisoformat(estado['ultima_fecha']):
        raise ValidationError('Fecha retroactiva anterior al ultimo corte contabilizado; requiere revision.', code='RETROACTIVE_REVIEW_REQUIRED')
    if foto.politica_causacion != 'DAILY_30' or foto.es_legado:
        raise ValidationError('La version contractual no admite esta causacion.')
    plan = json.loads(json.dumps(estado['plan']))
    restante = _dinero(recibido)
    lineas = []
    capital = intereses = CERO

    def causado_fila(fila):
        inicio, fin = date.fromisoformat(fila['corte']), date.fromisoformat(fila['fecha'])
        if fecha < inicio:
            return Decimal(fila.get('causado_neto', '0'))
        saldo = sum((_dinero(f['capital']) for f in plan if f['fecha'] >= fila['fecha']), CERO)
        def devengo(corte):
            return calcular_interes_causado_diario_30(saldo_capital=saldo,
                tasa_mensual_porcentaje=foto.tasa_interes_mensual, fecha_ultimo_corte=inicio,
                fecha_efectiva=max(inicio, min(corte, fin)), fecha_proximo_vencimiento=fin)[0]
        if 'fecha_causacion' in fila:
            anterior = date.fromisoformat(fila['fecha_causacion'])
            neto = Decimal(fila['causado_neto']) + devengo(fecha) - devengo(anterior)
        else:
            pagado = _dinero(fila['interes_original']) - _dinero(fila['interes'])
            neto = devengo(fecha) - pagado
        # Saved periods cap ordinary contractual interest: no overdue surcharges.
        return min(_dinero(fila['interes']), neto)

    causados = {id(f): causado_fila(f) for f in plan}

    def asignar(cuota, concepto, monto):
        nonlocal restante, capital, intereses
        monto = min(restante, _dinero(monto))
        if monto:
            lineas.append({'cuota': cuota.get('cuota') if cuota else None, 'concepto': concepto,
                           'importe': str(monto), 'periodo': cuota['fecha'] if cuota else fecha.isoformat()})
            restante -= monto
            if concepto == 'CAPITAL':
                capital += monto
            else:
                intereses += monto
        return monto

    if modo in ('FULL', 'PARTIAL'):
        if not plan:
            raise ValidationError('No quedan componentes pendientes en el libro.')
        objetivo = _dinero(plan[0]['capital']) + _dinero(plan[0]['interes'])
        if modo == 'FULL' and restante != objetivo:
            raise ValidationError('La cuota completa requiere coincidencia exacta, sin tolerancia de centavos.', code='INSTALLMENT_AMOUNT_MISMATCH')
        conceptos = ('interes', 'capital') if reglas['imputacion'] == 'INTEREST_FIRST' else ('capital', 'interes')
        for fila in plan[:1] if modo == 'FULL' else plan:
            for campo in conceptos:
                usado = asignar(fila, 'CAPITAL' if campo == 'capital' else 'INTEREST', fila[campo])
                fila[campo] = str(_dinero(fila[campo]) - usado)
                if campo == 'interes':
                    causados[id(fila)] -= usado
            if not restante:
                break
        nuevo_plan = [fila for fila in plan if _dinero(fila['capital']) + _dinero(fila['interes']) > 0]
        for fila in nuevo_plan:
            fila.update(causado_neto=str(causados[id(fila)]), fecha_causacion=fecha.isoformat())
        excluidos = CERO
    else:
        if any(causados[id(f)] < 0 for f in plan):
            raise ValidationError('Hay intereses anticipados en el periodo. Su tratamiento exige politica adicional; no se pierden ni se cobran de nuevo al reproyectar.', code='PREPAID_INTEREST_REVIEW_REQUIRED')
        if modo == 'PRINCIPAL' and any(date.fromisoformat(f['fecha']) < fecha for f in plan):
            raise ValidationError('Abono con cuotas vencidas requiere politica adicional; no se refinancia el atraso.', code='OVERDUE_PRINCIPAL_REVIEW_REQUIRED')
        # Each earned saved period is counted once; redundant extraction is irrelevant here.
        for fila in plan:
            causado = max(CERO, causados[id(fila)])
            usado = asignar(fila, 'INTEREST', causado)
            if usado < causado:
                raise ValidationError('El ingreso no cubre los intereses causados a la fecha.')
        saldo = _dinero(estado['capital'])
        if modo == 'PAYOFF' and restante < saldo:
            raise ValidationError('El ingreso no cubre la liquidacion; no hay tolerancia de un centavo.', code='PAYOFF_SHORTFALL')
        if modo == 'PRINCIPAL' and restante <= 0:
            raise ValidationError('El ingreso no produce un abono a capital.')
        asignar(None, 'CAPITAL', saldo)
        saldo -= capital
        excluidos = max(CERO, sum((_dinero(f['interes']) for f in plan), CERO) - intereses)
        futuros = [date.fromisoformat(f['fecha']) for f in plan if date.fromisoformat(f['fecha']) > fecha]
        primera = min(futuros) if futuros else sumar_meses(fecha, 1)
        corte = max((date.fromisoformat(f['corte']) for f in plan if date.fromisoformat(f['corte']) <= fecha), default=fecha)
        dias = 0 if any(date.fromisoformat(f['fecha']) == fecha for f in plan) else min(30, max(0, (fecha - corte).days))
        proyeccion = generar_plan_reduccion_plazo(
            principal=saldo, tasa_mensual_porcentaje=foto.tasa_interes_mensual,
            cuota_programada=foto.valor_cuota_estimada, primera_fecha_vencimiento=primera,
            dias_primer_periodo=30 - dias)
        if modo == 'PRINCIPAL':
            excluidos = max(CERO, excluidos - proyeccion.intereses_totales)
        # The existing motor rounds COP principal to pesos. Never silently discard bank cents.
        if sum((c.capital for c in proyeccion.cuotas), CERO) != saldo:
            raise ValidationError('El capital residual contiene centavos; falta aprobar su tratamiento en la reproyeccion.', code='RESIDUAL_CENTS_REVIEW_REQUIRED')
        nuevo_plan = [{'cuota': None, 'numero': c.numero, 'fecha': c.fecha_vencimiento.isoformat(),
                       'capital': str(c.capital), 'interes': str(c.interes), 'interes_original': str(c.interes),
                       'corte': (fecha if i == 0 else proyeccion.cuotas[i - 1].fecha_vencimiento).isoformat()}
                      for i, c in enumerate(proyeccion.cuotas)]
    if capital > _dinero(estado['capital']):
        raise ValidationError('El capital no puede quedar negativo.')
    if restante:
        lineas.append({'cuota': None, 'concepto': 'UNALLOCATED', 'importe': str(restante), 'periodo': fecha.isoformat()})
    nuevo = {**estado, 'plan': nuevo_plan, 'capital': str(_dinero(estado['capital']) - capital),
             'ultima_fecha': fecha.isoformat(), 'proyectado': estado['proyectado'] or modo in ('PRINCIPAL', 'PAYOFF')}
    return {'capital': str(capital), 'intereses': str(intereses), 'sin_destino': str(restante),
            'importe': str(_dinero(recibido)), 'lineas': lineas, 'despues': nuevo,
            'cuota_programada': str(foto.valor_cuota_estimada),
            'ultima_cuota': str(_dinero(nuevo_plan[-1]['capital']) + _dinero(nuevo_plan[-1]['interes'])) if nuevo_plan else '0.00',
            'intereses_futuros_excluidos': str(excluidos)}


def preparar_preview(*, ingreso_id, actor, modo, politica_id=None):
    habilitados()
    exigir_operador(actor)
    ingreso = ConciliacionRecaudoEducativo.objects.get(pk=ingreso_id)
    alcance = gobierno.exigir_alcance(actor, ingreso.reporte.solicitud.institucion_id, 'POST')
    _validar_ingreso(ingreso)
    if hasattr(ingreso, 'aplicacion'):
        raise ValidationError('Este ingreso ya tiene una aplicacion integra historica; se consulta sin copiarla al libro.')
    politica, reglas, version = _politica(ingreso, politica_id)
    estado = estado_libro(ingreso.reporte.fotografia)
    disponible = anotar_ingresos(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk)).get().disponible_libro
    if disponible <= 0:
        raise ValidationError('El ingreso no tiene dinero sin destino.')
    resultado = calcular_distribucion(foto=ingreso.reporte.fotografia, estado=estado,
        recibido=disponible, fecha=ingreso.fecha_efectiva, modo=modo, reglas=reglas)
    from financiacion_educativa.models_gobernanza_libro import ControlPilotoLibro
    control = ControlPilotoLibro.objects.get(pk=1)
    snapshot = {'motor': 'EDU_LEDGER_V1', 'ingreso': str(ingreso.pk), 'actor': str(actor.pk), 'modo': modo,
                'control_revision': control.revision, 'alcance': alcance.pk,
                'politica': politica.pk if politica else None, 'reglas': reglas, 'version': version,
                'estado': estado, 'resultado': resultado, 'fecha': ingreso.fecha_efectiva.isoformat(),
                'recibido': str(ingreso.monto_recibido), 'evidencia': ingreso.evidencia_movimiento.sha256}
    return snapshot, signing.dumps(snapshot, salt=SALT, compress=True)


def _cargar_token(token):
    try:
        return signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError('Vista previa vencida o alterada. Recarga y revisa de nuevo.') from None


@transaction.atomic
def contabilizar(*, ingreso_id, actor, token, clave, motivo, confirmar=False):
    habilitados()
    exigir_operador(actor)
    snapshot = _cargar_token(token)
    if not isinstance(snapshot, dict) or snapshot.get('motor') != 'EDU_LEDGER_V1' or not all(
        clave in snapshot for clave in ('ingreso', 'actor', 'modo', 'estado', 'resultado')):
        raise ValidationError('La vista previa no corresponde a una aplicacion. Recarga el ingreso.')
    if not snapshot.get('politica'):
        raise ValidationError('Contabilizacion 2B bloqueada: la simulacion no tiene politica aprobada.')
    if confirmar is not True or not motivo.strip() or len(motivo) > 1000:
        raise ValidationError('Confirmacion y motivo operativo son obligatorios.')
    if snapshot['ingreso'] != str(ingreso_id) or snapshot['actor'] != str(actor.pk):
        raise ValidationError('Vista previa de otro ingreso u operador.')
    ingreso_base = ConciliacionRecaudoEducativo.objects.get(pk=ingreso_id)
    institucion_id = ingreso_base.reporte.solicitud.institucion_id
    control = gobierno.bloquear_piloto(institucion_id)
    gobierno.exigir_contabilizacion(actor=actor, institucion_id=institucion_id,
        fecha=ingreso_base.fecha_efectiva, politica_id=snapshot['politica'], control=control)
    # Same global order as the existing complete application; no nullable joined locks.
    SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=ingreso_base.reporte.solicitud_id)
    ingreso = ConciliacionRecaudoEducativo.objects.select_for_update().get(pk=ingreso_id)
    clave = UUID(str(clave))
    huella = _hash({'snapshot': snapshot, 'motivo': motivo})
    repetido = MovimientoLibroEducativo.objects.filter(ingreso=ingreso, clave=clave).first()
    if repetido:
        if repetido.payload_sha256 != huella:
            raise ValidationError('La clave ya fue usada con otro contenido.')
        return repetido
    list(ArchivoRecaudoEducativo.objects.select_for_update().filter(pk__in=[
        pk for pk in (ingreso.evidencia_movimiento_id, ingreso.reporte.comprobante_id) if pk]).order_by('pk'))
    actual, _ = preparar_preview(ingreso_id=ingreso_id, actor=actor, modo=snapshot['modo'], politica_id=snapshot['politica'])
    if actual != snapshot:
        raise ValidationError('La obligacion o el ingreso cambio. Recarga la vista previa.')
    resultado = actual['resultado']
    for concepto, campo in (('CAPITAL', 'capital'), ('INTEREST', 'intereses'), ('UNALLOCATED', 'sin_destino')):
        if sum((_dinero(l['importe']) for l in resultado['lineas'] if l['concepto'] == concepto), CERO) != _dinero(resultado[campo]):
            raise ValidationError('La suma de lineas no corresponde a la cabecera.')
    if sum((_dinero(f['capital']) for f in resultado['despues']['plan']), CERO) != _dinero(resultado['despues']['capital']):
        raise ValidationError('La proyeccion no cuadra con el capital neto.')
    if Decimal(resultado['capital']) + Decimal(resultado['intereses']) == 0:
        raise ValidationError('No existe una aplicacion efectiva; el dinero sigue sin destino.')
    despues = resultado['despues']
    despues['secuencia'] = actual['estado']['secuencia'] + 1
    despues['gobernanza'] = {'politica_id': snapshot['politica'],
        'revision_piloto': snapshot['control_revision'], 'alcance_id': snapshot['alcance'], 'operacion': 'POST'}
    movimiento = MovimientoLibroEducativo.objects.create(
        ingreso=ingreso, fotografia=ingreso.reporte.fotografia, politica_id=snapshot['politica'],
        secuencia=despues['secuencia'], clave=clave, tipo=snapshot['modo'],
        importe=resultado['importe'], capital=resultado['capital'], intereses=resultado['intereses'],
        sin_destino=resultado['sin_destino'], fecha_bancaria=ingreso.fecha_efectiva, actor=actor,
        motivo=motivo, payload_sha256=huella, estado_antes=actual['estado'], estado_despues=despues)
    for linea in resultado['lineas']:
        LineaLibroEducativo.objects.create(movimiento=movimiento, cuota_id=linea['cuota'],
            concepto=linea['concepto'], importe=linea['importe'], referencia_periodo=linea['periodo'])
    return movimiento


def preparar_compensacion(*, movimiento_id, actor):
    habilitados()
    original = MovimientoLibroEducativo.objects.get(pk=movimiento_id)
    exigir_permiso(actor, PERMISO_CONSULTAR)
    politica, revision, alcance = gobierno.exigir_contabilizacion(actor=actor,
        institucion_id=original.fotografia.solicitud.institucion_id,
        fecha=original.fecha_bancaria, accion='COMPENSATE')
    if actor.pk == original.actor_id:
        raise PermissionDenied('La compensacion requiere un autorizante distinto del operador original.')
    ultimo = MovimientoLibroEducativo.objects.filter(fotografia=original.fotografia).order_by('-secuencia').first()
    if original.tipo == 'COMPENSATION' or ultimo.pk != original.pk or hasattr(original, 'compensacion'):
        raise ValidationError('Solo se admite compensar integramente el ultimo movimiento sin sucesores.')
    estado_libro(original.fotografia)
    snapshot = {'movimiento': str(original.pk), 'actor': str(actor.pk), 'secuencia': ultimo.secuencia,
        'politica': politica.pk, 'control_revision': revision, 'alcance': alcance,
        'original_hash': original.payload_sha256}
    return snapshot, signing.dumps(snapshot, salt=SALT, compress=True)


@transaction.atomic
def compensar(*, movimiento_id, actor, clave, motivo, secuencia_esperada, token=None):
    habilitados()
    exigir_permiso(actor, PERMISO_CONSULTAR)
    exigir_permiso(actor, 'financiacion_educativa.compensar_libro_educativo')
    snapshot = _cargar_token(token)
    if snapshot.get('movimiento') != str(movimiento_id) or snapshot.get('actor') != str(actor.pk):
        raise ValidationError('Vista previa de compensacion ajena o invalida.')
    original = MovimientoLibroEducativo.objects.get(pk=movimiento_id)
    institucion_id = original.fotografia.solicitud.institucion_id
    control = gobierno.bloquear_piloto(institucion_id)
    politica, _, _ = gobierno.exigir_contabilizacion(actor=actor, institucion_id=institucion_id,
        fecha=original.fecha_bancaria, politica_id=snapshot['politica'], accion='COMPENSATE', control=control)
    if actor.pk == original.actor_id:
        raise PermissionDenied('La compensacion requiere autorizacion independiente.')
    SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=original.fotografia.solicitud_id)
    ConciliacionRecaudoEducativo.objects.select_for_update().get(pk=original.ingreso_id)
    payload = _hash({'snapshot': snapshot, 'motivo': motivo, 'secuencia': secuencia_esperada})
    existente = MovimientoLibroEducativo.objects.filter(ingreso=original.ingreso, clave=clave).first()
    if existente:
        if existente.payload_sha256 != payload:
            raise ValidationError('Reintento de compensacion diferente.')
        return existente
    actual, _ = preparar_compensacion(movimiento_id=movimiento_id, actor=actor)
    if actual != snapshot:
        raise ValidationError('La politica, el piloto o el movimiento cambio. Recarga la compensacion.')
    ultimo = MovimientoLibroEducativo.objects.filter(fotografia=original.fotografia).order_by('-secuencia').first()
    if not motivo.strip() or len(motivo) > 1000 or original.tipo == 'COMPENSATION' or ultimo.pk != original.pk or ultimo.secuencia != secuencia_esperada or hasattr(original, 'compensacion'):
        raise ValidationError('Solo se admite compensar integramente el ultimo movimiento sin sucesores; requiere revision y motivo.')
    estado = {**original.estado_antes, 'secuencia': ultimo.secuencia + 1}
    estado['gobernanza'] = {'politica_id': politica.pk, 'revision_piloto': snapshot['control_revision'],
        'alcance_id': snapshot['alcance'], 'operacion': 'COMPENSATE'}
    movimiento = MovimientoLibroEducativo.objects.create(
        ingreso=original.ingreso, fotografia=original.fotografia, politica=politica,
        secuencia=estado['secuencia'], clave=clave, tipo='COMPENSATION', compensa=original,
        importe=original.importe, capital=original.capital, intereses=original.intereses,
        sin_destino=original.sin_destino, fecha_bancaria=original.fecha_bancaria, actor=actor,
        motivo=motivo, payload_sha256=payload, estado_antes=original.estado_despues, estado_despues=estado)
    for linea in original.lineas.all():
        LineaLibroEducativo.objects.create(movimiento=movimiento, cuota=linea.cuota, concepto=linea.concepto,
            importe=linea.importe, referencia_periodo=linea.referencia_periodo)
    return movimiento
