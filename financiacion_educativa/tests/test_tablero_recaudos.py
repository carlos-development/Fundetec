from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from instituciones.models import MembresiaInstitucion
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


class DatosTableroRecaudos(DatosIngresoOperaciones):
    def preparar_tablero(self):
        self.rango = {'desde': '2026-09-01', 'hasta': '2026-09-30'}
        self.aplicado = self.movimiento(self.solicitud, date(2026, 9, 1), aplicar=True)
        self.pendiente = self.movimiento(self.solicitud, date(2026, 9, 30), monto=Decimal('40000'))
        self.ajena, self.foto_ajena, _, _ = self.contrato('OTRA-INSTITUCION-SINTETICA', self.otra)
        self.aplicado_ajeno = self.movimiento(self.ajena, date(2026, 9, 15), aplicar=True)
        self.pendiente_ajeno = self.movimiento(self.ajena, date(2026, 9, 30), monto=Decimal('60000'))
        self.anterior = self.movimiento(self.solicitud, date(2026, 8, 31), monto=Decimal('8000'))
        self.posterior = self.movimiento(self.solicitud, date(2026, 10, 1), monto=Decimal('9000'))
        self.aplicado_segundo = self.movimiento(self.solicitud, date(2026, 9, 30), aplicar=True)
        self.sin_confirmar, _ = self.reportar(monto='12345')
        self.rechazado, _ = self.reportar(monto='23456')
        recaudos.rechazar_reporte(reporte_id=self.rechazado.pk, actor=self.actor, motivo='Soporte sintetico no conciliable.')
        self.confirmados_rango = [self.aplicado, self.pendiente, self.aplicado_ajeno,
            self.pendiente_ajeno, self.aplicado_segundo]

    def movimiento(self, solicitud, fecha, *, monto=None, aplicar=False):
        if monto is None:
            fotografia = solicitud.fotografias_financieras.filter(activa=True).get()
            monto = fotografia.cuotas.filter(aplicacion_recaudo__isnull=True).order_by('numero').first().valor_cuota
        ingreso, _ = self.registrar(solicitud_id=solicitud.pk, fecha=fecha, monto=monto,
            referencia='SYNTHETIC-' + uuid4().hex, clave=uuid4())
        if aplicar:
            self.aplicar_movimiento(ingreso)
        return ingreso

    def aplicar_movimiento(self, ingreso):
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)

    def ruta_tablero(self):
        return reverse('financiacion_educativa_web:operaciones:tablero-recaudos')


@override_settings(**AISLAMIENTO)
class TableroRecaudosTests(DatosTableroRecaudos, TestCase):
    def setUp(self):
        super().setUp()
        self.preparar_tablero()
        self.client.force_login(self.actor)

    def consultar(self, **datos):
        return self.client.get(self.ruta_tablero(), {**self.rango, **datos})

    def test_importes_y_desglose_solo_de_conciliaciones_y_aplicaciones(self):
        respuesta = self.consultar()
        self.assertEqual(respuesta.status_code, 200)
        totales = respuesta.context['totales']
        self.assertEqual(totales['confirmados'], sum(i.monto_recibido for i in self.confirmados_rango))
        self.assertEqual(totales['pendientes'], Decimal('100000.00'))
        self.assertEqual(totales['cantidad_confirmados'], 5)
        self.assertEqual(totales['cantidad_pendientes'], 2)
        self.assertEqual(totales['cantidad_aplicados'], 3)
        self.assertEqual(totales['confirmados'], totales['pendientes'] + totales['aplicados'])
        aplicaciones = AplicacionCuotaEducativa.objects.all()
        self.assertEqual(totales['capital'], sum(a.capital_aplicado for a in aplicaciones))
        self.assertEqual(totales['intereses'], sum(a.interes_aplicado for a in aplicaciones))
        self.assertEqual(totales['aplicados'], totales['capital'] + totales['intereses'])
        self.assertEqual(totales['capital'], self.aplicado.aplicacion.capital_aplicado +
            self.aplicado_segundo.aplicacion.capital_aplicado + self.aplicado_ajeno.aplicacion.capital_aplicado)

    def test_cada_indicador_reconcilia_importe_y_cantidad_con_su_listado(self):
        for institucion in (None, str(self.institucion.pk), str(self.otra.pk)):
            respuesta = self.consultar(**({'institucion': institucion} if institucion else {}))
            for indicador in respuesta.context['indicadores']:
                with self.subTest(institucion=institucion, indicador=indicador['codigo']):
                    respaldo = self.client.get(indicador['url'])
                    self.assertEqual(respaldo.status_code, 200)
                    self.assertEqual(indicador['cantidad'], respaldo.context['pagina'].paginator.count)
                    self.assertEqual(indicador['importe'], respaldo.context['total_listado'])
                    self.assertEqual(indicador['importe'], sum(i.monto_recibido for i in respaldo.context['pagina']))

    def test_institucion_limita_todas_las_cifras_meses_y_listado(self):
        respuesta = self.consultar(institucion=self.institucion.pk)
        ids = {i.pk for i in respuesta.context['pagina']}
        self.assertEqual(ids, {self.aplicado.pk, self.pendiente.pk, self.aplicado_segundo.pk})
        self.assertEqual(respuesta.context['totales']['pendientes'], Decimal('40000'))
        self.assertNotContains(respuesta, self.ajena.referencia_externa)
        self.assertEqual(respuesta.context['meses'][0]['confirmados'], respuesta.context['totales']['confirmados'])

    def test_fechas_limite_inclusivas_usan_fecha_bancaria_no_creacion(self):
        self.assertNotEqual(self.aplicado.reporte.creado_en.date(), self.aplicado.fecha_efectiva)
        for dia, esperados in [('2026-09-01', {self.aplicado.pk}),
                              ('2026-09-30', {self.pendiente.pk, self.pendiente_ajeno.pk, self.aplicado_segundo.pk})]:
            respuesta = self.consultar(desde=dia, hasta=dia)
            self.assertEqual({i.pk for i in respuesta.context['pagina']}, esperados)
        respuesta = self.consultar()
        self.assertNotIn(self.anterior.pk, {i.pk for i in respuesta.context['pagina']})
        self.assertNotIn(self.posterior.pk, {i.pk for i in respuesta.context['pagina']})

    def test_meses_y_cohorte_completa_no_duplican_fotografia_con_varias_cuotas(self):
        respuesta = self.consultar(desde='2026-08-01', hasta='2026-10-31')
        meses, totales = respuesta.context['meses'], respuesta.context['totales']
        self.assertEqual([m['mes'].month for m in meses], [8, 9, 10])
        for campo in ('confirmados', 'pendientes', 'aplicados', 'capital', 'intereses'):
            self.assertEqual(totales[campo], sum(m[campo] for m in meses))
        self.assertEqual(totales['cantidad_confirmados'], 7)

    def test_pendiente_no_fabrica_capital_intereses_ni_cuota_pagada(self):
        respuesta = self.consultar(estado='TO_APPLY')
        self.assertEqual({i.pk for i in respuesta.context['pagina']}, {self.pendiente.pk, self.pendiente_ajeno.pk})
        self.assertNotContains(respuesta, 'Aplicado &middot; Cuota')
        self.assertContains(respuesta, 'a&uacute;n no tienen desglose')
        self.assertEqual(respuesta.context['total_listado'], Decimal('100000'))

    def test_aplicacion_posterior_mueve_importe_sin_alterar_confirmado(self):
        ingreso = self.movimiento(self.solicitud, date(2026, 9, 20))
        antes = self.consultar().context['totales']
        self.aplicar_movimiento(ingreso)
        despues = self.consultar().context['totales']
        self.assertEqual(antes['confirmados'], despues['confirmados'])
        self.assertEqual(antes['pendientes'] - ingreso.monto_recibido, despues['pendientes'])
        self.assertEqual(antes['aplicados'] + ingreso.monto_recibido, despues['aplicados'])

    def test_vacio_muestra_ceros_y_respaldo_vacio(self):
        respuesta = self.consultar(desde='2020-01-01', hasta='2020-01-31')
        self.assertEqual(respuesta.context['totales']['confirmados'], Decimal('0'))
        self.assertEqual(respuesta.context['meses'], [])
        self.assertEqual(respuesta.context['pagina'].paginator.count, 0)
        self.assertContains(respuesta, 'Sin movimientos en este listado')

    def test_filtros_invalidos_fallan_cerrados_sin_importes(self):
        for datos in ({'desde': 'no-fecha'}, {'hasta': ''}, {'estado': 'PENDING'},
                      {'desde': '2026-10-01', 'hasta': '2026-09-01'}, {'institucion': uuid4()}):
            with self.subTest(datos=datos):
                respuesta = self.consultar(**datos)
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotIn('totales', respuesta.context)
                self.assertNotContains(respuesta, self.solicitud.referencia_externa, status_code=400)

    def test_default_mes_actual_y_limpiar_sin_parametros(self):
        respuesta = self.client.get(self.ruta_tablero())
        hoy = timezone.localdate()
        self.assertEqual(respuesta.context['form'].cleaned_data['desde'], hoy.replace(day=1))
        self.assertEqual(respuesta.context['form'].cleaned_data['hasta'], hoy)
        self.assertContains(respuesta, f'href="{self.ruta_tablero()}">Limpiar')

    def test_paginacion_conserva_cohorte_y_total_completo(self):
        for _ in range(26):
            self.movimiento(self.solicitud, date(2026, 9, 20), monto=Decimal('123.45'))
        respuesta = self.consultar(estado='TO_APPLY', institucion=self.institucion.pk, page=2)
        pagina = respuesta.context['pagina']
        self.assertEqual(pagina.paginator.count, 27)
        self.assertEqual(len(pagina), 2)
        self.assertEqual(respuesta.context['total_listado'], Decimal('40000') + 26 * Decimal('123.45'))
        parametros = parse_qs(respuesta.context['querystring'])
        self.assertEqual(parametros['institucion'], [str(self.institucion.pk)])
        self.assertEqual(parametros['estado'], ['TO_APPLY'])
        self.assertEqual(parametros['desde'], [self.rango['desde']])
        self.assertNotIn('page', parametros)

    def test_consulta_requiere_ambos_permisos_no_staff_y_no_otorga_acciones(self):
        acceso = aplicacion_cuotas.PERMISO_ACCESO
        for permisos in ((), (acceso,), (recaudos.PERMISO_CONSULTAR,)):
            usuario = self.operador(permisos)
            usuario.is_staff = True
            usuario.save(update_fields=['is_staff'])
            self.client.force_login(usuario)
            self.assertEqual(self.consultar().status_code, 403)
        usuario = self.operador((acceso, recaudos.PERMISO_CONSULTAR))
        self.client.force_login(usuario)
        self.assertEqual(self.consultar().status_code, 200)
        aplicar = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[self.pendiente.reporte_id])
        self.assertEqual(self.client.post(aplicar, {}).status_code, 403)

    def test_estudiante_e_institucion_no_pueden_entrar_por_url_directa(self):
        for usuario in (self.pagador, get_user_model().objects.create_user('institucion', 'institution@example.test', 'test-only')):
            MembresiaInstitucion.objects.create(usuario=usuario, institucion=self.institucion, rol='INSTITUTION_ADMIN')
            self.client.force_login(usuario)
            self.assertEqual(self.consultar(institucion=self.otra.pk).status_code, 403)
        self.client.logout()
        self.assertEqual(self.consultar().status_code, 302)

    def test_solo_get_flag_y_cache_privada(self):
        self.assertEqual(self.client.post(self.ruta_tablero(), self.rango).status_code, 405)
        self.assertIn('no-store', self.consultar()['Cache-Control'])
        with self.settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False):
            self.assertEqual(self.consultar().status_code, 404)

    def test_sin_pii_evidencia_referencias_bancarias_ni_mutaciones(self):
        self.solicitud.numero_documento_estudiante = '9090909090'
        self.solicitud.save(update_fields=['numero_documento_estudiante'])
        antes = list(ConciliacionRecaudoEducativo.objects.values()), list(AplicacionCuotaEducativa.objects.values())
        respuesta = self.consultar()
        for valor in (self.banco.archivo.name, str(self.banco.pk), self.aplicado.referencia_verificada,
                      self.pagador.email, self.solicitud.numero_documento_estudiante):
            self.assertNotContains(respuesta, valor)
        self.assertEqual(antes, (list(ConciliacionRecaudoEducativo.objects.values()), list(AplicacionCuotaEducativa.objects.values())))
        self.assertContains(respuesta, 'Tablero de recaudos')

    def test_resumen_usa_una_consulta_sin_locks_ni_union_multiplicadora(self):
        from financiacion_educativa.dashboards.operaciones.reporte_recaudos import cohorte_bancaria, construir_tablero
        datos = {'desde': date(2026, 9, 1), 'hasta': date(2026, 9, 30), 'estado': 'CONFIRMED'}
        with CaptureQueriesContext(connection) as capturadas:
            tablero = construir_tablero(cohorte_bancaria(datos), datos)
        self.assertEqual(len(capturadas), 1)
        self.assertNotIn('FOR UPDATE', capturadas[0]['sql'])
        self.assertEqual(tablero['totales']['cantidad_confirmados'], 5)
