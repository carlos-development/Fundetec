import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.dashboards.operaciones.permissions import PERMISO_ACCESO, PERMISO_SOLICITUDES
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.templatetags.recaudo_format import cop_recaudo
from financiacion_educativa.tests.test_aplicacion_cuotas import DatosAplicacion
from financiacion_educativa.tests.test_libro_pagos import DatosLibro
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


class ConsultaCapitalMixin:
    def verificar_solo_lectura(self, execute, sql, params, many, context):
        if sql.startswith('SELECT') and 'financiacion_educativa_aplicacioncuotaeducativa' in sql:
            execute('SHOW transaction_read_only', None, False, context)
            self.assertEqual(context['cursor'].fetchone()[0], 'on')
        return execute(sql, params, many, context)

    def consultar(self, obligacion_id, fecha):
        output = StringIO()
        with connection.execute_wrapper(self.verificar_solo_lectura):
            call_command('consultar_capital_pendiente', '--obligacion-id', str(obligacion_id),
                '--fecha-corte', fecha.isoformat(), stdout=output)
        return json.loads(output.getvalue())

    def detalle(self, fotografia, fecha):
        with connection.execute_wrapper(self.verificar_solo_lectura):
            return self.client.get(self.detalle_url(fotografia.solicitud), {'fecha_corte': fecha})


@skipUnless(connection.vendor == 'postgresql', 'La consulta requiere PostgreSQL.')
@override_settings(**{**AISLAMIENTO, 'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': False})
class CapitalPendienteCorteTests(ConsultaCapitalMixin, DatosAplicacion, TransactionTestCase):
    def crear_caso(self, referencia, fecha_bancaria=None, *, aplicar=False, autorizada_en):
        with patch('financiacion_educativa.tests.test_dashboard_obligaciones.timezone.now', return_value=autorizada_en):
            self.solicitud, self.fotografia, _, _ = self.contrato(referencia)
        self.solicitud.usuario = self.pagador
        self.solicitud.correo = self.pagador.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        cuota = self.fotografia.cuotas.order_by('numero').first()
        if fecha_bancaria is not None:
            reporte, _ = self.reportar(monto=cuota.valor_cuota, fecha=fecha_bancaria)
            self.escanear(reporte.comprobante_id)
            recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte, referencia=uuid4().hex, fecha=fecha_bancaria))
            if aplicar:
                cuota, sello = aplicacion_cuotas.preparar_vista_previa(reporte=reporte, actor=self.actor)
                aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=cuota.pk, actor=self.actor,
                    vista_previa=sello, confirmar_cuota=True)
        return self.fotografia, cuota

    def test_detalle_y_comando_usan_fecha_bancaria_inclusiva(self):
        hoy = timezone.localdate()
        corte = hoy - timedelta(days=1)
        antes = corte - timedelta(days=1)
        firma = datetime.combine(antes - timedelta(days=1), time(9), ZoneInfo('America/Bogota'))
        casos = [
            ('sin_pagos', *self.crear_caso('CORTE-SIN-PAGOS', autorizada_en=firma), False),
            ('aplicado_antes', *self.crear_caso('CORTE-ANTES', antes, aplicar=True, autorizada_en=firma), True),
            ('aplicado_despues', *self.crear_caso('CORTE-DESPUES', hoy, aplicar=True, autorizada_en=firma), False),
            ('conciliado_pendiente', *self.crear_caso('CORTE-CONCILIADO', antes, autorizada_en=firma), False),
            ('aplicado_el_mismo_dia', *self.crear_caso('CORTE-INCLUSIVO', corte, aplicar=True, autorizada_en=firma), True),
        ]
        cantidades = (ConciliacionRecaudoEducativo.objects.count(), AplicacionCuotaEducativa.objects.count())
        self.assertEqual(cantidades, (4, 3))
        self.client.force_login(self.actor)
        respuestas = {}
        for nombre, foto, cuota, aplicado in casos:
            with self.subTest(caso=nombre):
                respuesta = self.consultar(foto.pk, corte)
                capital_aplicado = cuota.capital if aplicado else Decimal('0.00')
                self.assertEqual(Decimal(respuesta['capital_financiado']), foto.capital_financiado)
                self.assertEqual(Decimal(respuesta['capital_aplicado']), capital_aplicado)
                self.assertEqual(Decimal(respuesta['capital_pendiente']), foto.capital_financiado - capital_aplicado)
                vista = self.detalle(foto, corte.isoformat())
                self.assertContains(vista, 'data-capital-corte')
                self.assertContains(vista, f'value="{corte.isoformat()}"')
                for clave in ('capital_financiado', 'capital_aplicado', 'capital_pendiente'):
                    self.assertEqual(vista.context[clave], Decimal(respuesta[clave]))
                    self.assertContains(vista, cop_recaudo(Decimal(respuesta[clave])))
                self.assertContains(vista, 'Pagada', count=int(aplicado))
                respuesta.pop('obligacion_id')
                respuestas[nombre] = dict(respuesta, http=vista.status_code)
        _, foto, cuota, _ = casos[1]
        self.assertEqual(Decimal(self.consultar(foto.pk, antes)['capital_aplicado']), cuota.capital)
        self.assertEqual(cantidades,
            (ConciliacionRecaudoEducativo.objects.count(), AplicacionCuotaEducativa.objects.count()))
        print('CAPITAL_CORTE=' + json.dumps(respuestas, sort_keys=True))

    def test_detalle_y_comando_respetan_el_dia_de_autorizacion_en_bogota(self):
        firma = datetime(2026, 10, 15, 2, 30, tzinfo=UTC)
        fecha_autorizacion = date(2026, 10, 14)
        anterior = fecha_autorizacion - timedelta(days=1)
        foto, _ = self.crear_caso('CORTE-AUTORIZACION', autorizada_en=firma)
        self.client.force_login(self.actor)

        with timezone.override('UTC'):
            rechazada = self.detalle(foto, anterior.isoformat())
            self.assertContains(rechazada, 'Elige el 14/10/2026 o una fecha posterior', status_code=400)
            self.assertContains(rechazada, '14/10/2026 21:30', status_code=400)
            self.assertContains(rechazada, f'value="{anterior.isoformat()}"', status_code=400)
            for texto in ('data-capital-corte', 'Desglose contractual en COP', 'Saldo inicial programado'):
                self.assertNotContains(rechazada, texto, status_code=400)

            salida = StringIO()
            with self.assertRaisesMessage(CommandError,
                    'La fecha de corte no puede ser anterior a la autorización efectiva del plan (2026-10-14, hora de Bogotá).'):
                call_command('consultar_capital_pendiente', '--obligacion-id', str(foto.pk),
                    '--fecha-corte', anterior.isoformat(), stdout=salida)
            self.assertEqual(salida.getvalue(), '')

            aceptada = self.detalle(foto, fecha_autorizacion.isoformat())
            self.assertContains(aceptada, 'data-capital-corte')
            self.assertContains(aceptada, 'Desglose contractual en COP')
            resultado = self.consultar(foto.pk, fecha_autorizacion)
            for clave, monto in (
                ('capital_financiado', foto.capital_financiado),
                ('capital_aplicado', Decimal('0.00')),
                ('capital_pendiente', foto.capital_financiado),
            ):
                self.assertEqual(aceptada.context[clave], monto)
                self.assertEqual(Decimal(resultado[clave]), monto)

        print('CAPITAL_CORTE_AUTORIZACION=' + json.dumps(dict(
            firma_utc=firma.isoformat(), fecha_autorizacion_bogota=fecha_autorizacion.isoformat(),
            anterior=dict(fecha_corte=anterior.isoformat(), http=rechazada.status_code,
                cifras_visibles=False, comando='CommandError'),
            mismo_dia=dict(fecha_corte=fecha_autorizacion.isoformat(), http=aceptada.status_code,
                cifras_visibles=True, comando='OK'))))

    def test_acceso_vigente_y_fecha_invalida(self):
        ruta = self.detalle_url(self.solicitud)
        self.assertEqual(self.client.get(ruta).status_code, 302)
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(ruta).status_code, 403)
        self.client.force_login(self.actor)
        respuesta = self.client.get(ruta, {'fecha_corte': '2026-02-31'})
        self.assertContains(respuesta, 'Indica una fecha válida.', status_code=400)
        self.assertNotContains(respuesta, 'data-capital-corte', status_code=400)
        self.assertEqual(self.client.post(ruta, {'fecha_corte': '2026-02-01'}).status_code, 405)


@skipUnless(connection.vendor == 'postgresql', 'La consulta requiere PostgreSQL.')
@override_settings(**{**AISLAMIENTO, 'FINANCIACION_EDUCATIVA_LIBRO_ENABLED': True,
    'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': False})
class CapitalPendienteLibroCorteTests(ConsultaCapitalMixin, DatosLibro, TransactionTestCase):
    def setUp(self):
        firma = datetime(2026, 7, 1, 15, tzinfo=UTC)
        with patch('financiacion_educativa.tests.test_dashboard_obligaciones.timezone.now', return_value=firma):
            super().setUp()

    def test_movimiento_libro_al_corte_deriva_sin_mostrar_saldo_actual(self):
        ingreso = self.nuevo_ingreso()
        self.registrar(ingreso)
        movimiento = ingreso.movimientos_libro.get()
        self.client.force_login(self.actor)
        corte = movimiento.fecha_bancaria
        vista = self.detalle(self.fotografia, corte.isoformat())
        self.assertContains(vista, 'La vista de Libro 2B muestra el estado actual.')
        libro = reverse('financiacion_educativa_web:operaciones:libro-ingreso', args=[ingreso.pk])
        self.assertContains(vista, f'href="{libro}"')
        self.assertNotContains(vista, 'data-capital-corte')
        self.assertNotContains(vista, 'Sin pago aplicado')
        with self.assertRaisesMessage(CommandError, 'Consulte el libro ampliado'):
            self.consultar(self.fotografia.pk, corte)
        dia_anterior = corte - timedelta(days=1)
        self.assertContains(self.detalle(self.fotografia, dia_anterior.isoformat()), 'data-capital-corte')

        lector = get_user_model().objects.create_user('lector-obligaciones')
        lector.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa',
            codename__in=[p.rsplit('.', 1)[1] for p in (PERMISO_ACCESO, PERMISO_SOLICITUDES)]))
        self.client.force_login(lector)
        vista_lector = self.detalle(self.fotografia, corte.isoformat())
        self.assertContains(vista_lector, 'un operador con acceso a Recaudos')
        self.assertNotContains(vista_lector, libro)
        self.assertEqual(self.client.get(libro).status_code, 403)
        self.assertEqual(ingreso.movimientos_libro.count(), 1)
        print('CAPITAL_CORTE_LIBRO=' + json.dumps(dict(http=vista.status_code, fecha_corte=corte.isoformat(),
            capital_historico_visible=False, deriva_a_libro=True, detalle_sin_permiso_recaudos=vista_lector.status_code,
            libro_sin_permiso_recaudos=403)))
