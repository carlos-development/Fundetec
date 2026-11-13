"""Bounded read-only diagnostics: no files, identities, bank references or writes."""
import json
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from instituciones.models import Institucion
from financiacion_educativa.models_gobernanza_libro import ControlPilotoLibro
from financiacion_educativa.models_libro import PoliticaLibroEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services import gobernanza_libro as gobierno, libro_pagos as libro
from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas


class Command(BaseCommand):
    help = 'Diagnostico 2B de solo lectura por institucion y cohorte bancaria; no abre soportes.'

    def add_arguments(self, parser):
        parser.add_argument('--institucion-id', required=True)
        parser.add_argument('--desde')
        parser.add_argument('--hasta')
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--modo', choices=dict(libro.MODOS), default='PARTIAL')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **opciones):
        if connection.in_atomic_block:
            raise CommandError('Ejecute el diagnostico fuera de una transaccion existente.')
        try:
            desde = date.fromisoformat(opciones['desde']) if opciones['desde'] else timezone.localdate()
            hasta = date.fromisoformat(opciones['hasta']) if opciones['hasta'] else desde
            if desde > hasta or not 1 <= opciones['limit'] <= 500:
                raise ValueError
        except ValueError:
            raise CommandError('Rango de fechas o limite invalido.') from None
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            try:
                institucion = Institucion.objects.get(pk=opciones['institucion_id'])
                actor = get_user_model().objects.get(pk=opciones['actor_id']) if opciones['actor_id'] else None
            except (Institucion.DoesNotExist, get_user_model().DoesNotExist, ValidationError):
                raise CommandError('Institucion u operador no disponibles.') from None
            consulta = ConciliacionRecaudoEducativo.objects.filter(reporte__solicitud__institucion=institucion,
                fecha_efectiva__gte=desde, fecha_efectiva__lte=hasta)
            totales = libro.resumen_ingresos(consulta)
            control = ControlPilotoLibro.objects.filter(pk=1).first()
            try:
                politica = gobierno.resolver_politica(institucion.pk, hasta)
                estado_politica = {'id': politica.pk, 'version': politica.version, 'sintetica': politica.es_sintetica,
                    'desde': politica.vigente_desde, 'hasta': politica.vigente_hasta}
            except ValidationError as error:
                estado_politica = {'bloqueo': getattr(error, 'code', None) or 'POLICY_REVIEW_REQUIRED'}
            casos, bloqueos = [], {}
            ingresos = libro.anotar_ingresos(consulta).select_related(
                'reporte__solicitud', 'reporte__fotografia', 'aplicacion', 'evidencia_movimiento'
            ).prefetch_related('evidencia_movimiento__escaneos').order_by('fecha_efectiva', 'pk')[:opciones['limit']]
            for ingreso in ingresos:
                codigo = 'READ_ONLY_NOT_APPROVAL'
                try:
                    if actor:
                        gobierno.exigir_contabilizacion(actor=actor, institucion_id=institucion.pk,
                            fecha=ingreso.fecha_efectiva)
                    if ingreso.disponible_libro <= 0:
                        codigo = 'HISTORICAL_COUNTED_ONCE' if hasattr(ingreso, 'aplicacion') else 'APPLIED'
                    else:
                        politica_caso = gobierno.resolver_politica(institucion.pk, ingreso.fecha_efectiva)
                        if not obligaciones_autorizadas().filter(pk=ingreso.reporte.fotografia_id).exists():
                            codigo = 'CONTRACT_NOT_AUTHORIZED'
                        elif ingreso.evidencia_movimiento.estado_escaneo != 'SAFE':
                            codigo = 'SECURITY_REVIEW_REQUIRED'
                        else:
                            libro.calcular_distribucion(foto=ingreso.reporte.fotografia,
                                estado=libro.estado_libro(ingreso.reporte.fotografia), recibido=ingreso.disponible_libro,
                                fecha=ingreso.fecha_efectiva, modo=opciones['modo'], reglas=politica_caso.reglas)
                except PermissionDenied:
                    codigo = 'OPERATOR_PERMISSION_OR_SCOPE_DENIED'
                except ValidationError as error:
                    codigo = getattr(error, 'code', None) or 'FINANCIAL_REVIEW_REQUIRED'
                bloqueos[codigo] = bloqueos.get(codigo, 0) + 1
                casos.append({'ingreso_id': ingreso.pk, 'fecha_bancaria': ingreso.fecha_efectiva,
                    'fecha_registro': ingreso.confirmada_en, 'estado': codigo,
                    'recibido': ingreso.monto_recibido, 'capital_neto': ingreso.capital_neto,
                    'intereses_netos': ingreso.interes_neto, 'sin_destino': ingreso.disponible_libro})
            salida = {'solo_lectura': True, 'no_revalida_archivos_ni_sustituye_confirmacion': True,
                'ambiente': settings.DEPLOYMENT_ENVIRONMENT,
                'flag': settings.FINANCIACION_EDUCATIVA_LIBRO_ENABLED,
                'institucion_id': institucion.pk, 'desde': desde, 'hasta': hasta,
                'piloto_activo': bool(control and control.activo),
                'institucion_piloto_id': control.institucion_id if control else None,
                'revision_control': control.revision if control else None,
                'politica_a_fecha_hasta': estado_politica,
                'politicas_historicas_sin_gobernanza': PoliticaLibroEducativo.objects.filter(
                    institucion=institucion, propuesta__isnull=True).count(),
                'totales': totales, 'cantidad_ingresos': consulta.count(), 'evaluados': len(casos),
                'cuadra': totales['recibido'] == totales['capital'] + totales['intereses'] + totales['sin_destino'],
                'clasificacion': bloqueos, 'casos': casos}
            self.stdout.write(json.dumps(salida, default=str, sort_keys=True))
