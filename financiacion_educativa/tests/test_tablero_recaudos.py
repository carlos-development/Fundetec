from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from instituciones.models import MembresiaInstitucion
from financiacion_educativa.dashboards.operaciones.reparto_intereses import repartir_cuotas
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.models_reparto_intereses import ConvenioRepartoInteresEducativo
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.templatetags.recaudo_format import cop_recaudo


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

    def crear_convenio(self, **cambios):
        datos = dict(institucion=self.institucion, version='SYNTHETIC-ONLY',
            vigente_desde=date(2026, 9, 1), vigente_hasta=date(2026, 9, 30),
            porcentaje_aprobado=Decimal('37.5000'), aprobado_por=self.actor,
            referencia_aprobacion='Prueba sintética')
        return ConvenioRepartoInteresEducativo.objects.create(**{**datos, **cambios})


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

    def test_interes_aplicado_y_partes_sin_politica_de_reparto(self):
        respuesta = self.consultar(institucion=self.institucion.pk, estado='TO_APPLY')
        esperado = self.aplicado.aplicacion.interes_aplicado + self.aplicado_segundo.aplicacion.interes_aplicado
        self.assertGreater(esperado, 0)
        self.assertContains(respuesta, f'<dd id="interes-aplicado-periodo">{cop_recaudo(esperado)}</dd>', html=True)
        self.assertContains(respuesta, '<dt>Parte de Aprobado</dt><dd>Sin política aprobada</dd>', html=True)
        self.assertContains(respuesta, '<dt>Parte de la institución</dt><dd>Sin política aprobada</dd>', html=True)

    def test_partes_por_cuota_aplicada_historica_y_ausentes_en_pendientes(self):
        aplicada = self.consultar(institucion=self.institucion.pk, estado='APPLIED')
        self.assertContains(aplicada, 'Cuota 1 cubierta')
        self.assertContains(aplicada, 'Cuota 2 cubierta')
        self.assertContains(aplicada, 'class="collections-installment-shares"', count=2)
        self.assertContains(aplicada, 'Sin política aprobada', count=6)
        pendiente = self.consultar(institucion=self.institucion.pk, estado='TO_APPLY')
        self.assertNotContains(pendiente, 'class="collections-installment-shares"')

    def test_porcentajes_del_convenio_en_el_resumen_y_el_movimiento(self):
        self.crear_convenio(porcentaje_aprobado=Decimal('0'))
        respuesta = self.consultar(institucion=self.institucion.pk, estado='APPLIED')
        self.assertEqual(respuesta.context['tasa_aprobado'], Decimal('0'))
        self.assertContains(respuesta, '0 %</span>')
        self.assertContains(respuesta, '100 %</span>')
        self.assertContains(respuesta, self.institucion.nombre_comercial)
        self.assertFalse(respuesta.context['varias_tasas'])

    def test_reparte_por_institucion_y_vigencia_y_cuadra_cada_periodo(self):
        self.crear_convenio(vigente_hasta=date(2026, 9, 29), porcentaje_aprobado=Decimal('0'))
        self.crear_convenio(version='SYNTHETIC-NEXT', vigente_desde=date(2026, 9, 30),
            vigente_hasta=date(2026, 10, 31), porcentaje_aprobado=Decimal('100'))
        siguiente = self.movimiento(self.solicitud, date(2026, 10, 1), aplicar=True)
        respuesta = self.consultar(hasta='2026-10-31')
        primero = self.aplicado.aplicacion.interes_aplicado
        segundo = self.aplicado_segundo.aplicacion.interes_aplicado
        otro = self.aplicado_ajeno.aplicacion.interes_aplicado
        posterior = siguiente.aplicacion.interes_aplicado

        self.assertTrue(respuesta.context['varias_tasas'])
        self.assertIsNone(respuesta.context['tasa_aprobado'])
        filas = {ingreso.pk: ingreso for ingreso in respuesta.context['pagina']}
        self.assertEqual(filas[self.aplicado.pk].cuotas_aplicadas[0]['reparto'],
            {'aprobado': Decimal('0'), 'institucion': primero})
        self.assertEqual(filas[self.aplicado_segundo.pk].cuotas_aplicadas[0]['reparto'],
            {'aprobado': segundo, 'institucion': Decimal('0')})
        self.assertNotIn('reparto', filas[self.aplicado_ajeno.pk].cuotas_aplicadas[0])
        self.assertEqual(respuesta.context['reparto_periodo'], {'aprobado': segundo + posterior, 'institucion': primero})
        self.assertEqual(respuesta.context['interes_sin_convenio'], otro)
        self.assertContains(respuesta, 'Sin política aprobada')
        septiembre, octubre = respuesta.context['meses']
        self.assertEqual(septiembre['reparto'], {'aprobado': segundo, 'institucion': primero})
        self.assertEqual(octubre['reparto'], {'aprobado': posterior, 'institucion': Decimal('0')})
        for mes in (septiembre, octubre):
            self.assertEqual(sum(mes['reparto'].values()) + mes['interes_sin_convenio'], mes['intereses'].quantize(Decimal('.01')))

    def test_redondeo_distribuye_centavos_entre_cuotas_sin_cambiar_total_del_ingreso(self):
        convenio = self.crear_convenio(porcentaje_aprobado=Decimal('50'))
        ingreso = SimpleNamespace(reporte=self.aplicado.reporte, fecha_efectiva=self.aplicado.fecha_efectiva,
            tiene_aplicacion_registrada=True, interes_neto=Decimal('.03'),
            cuotas_aplicadas=[{'interes_neto': Decimal('.01')} for _ in range(3)])
        repartir_cuotas(ingreso, {self.institucion.pk: [convenio]})
        partes = [cuota['reparto'] for cuota in ingreso.cuotas_aplicadas]
        self.assertTrue(all(sum(parte.values()) == Decimal('.01') for parte in partes))
        self.assertEqual(sum(parte['aprobado'] for parte in partes), Decimal('.02'))
        self.assertEqual(sum(parte['institucion'] for parte in partes), Decimal('.01'))

    def test_total_redondea_por_ingreso_y_no_por_periodo_ni_pagina(self):
        self.crear_convenio()
        ingresos = [self.aplicado, self.aplicado_segundo]
        ingresos.extend(self.movimiento(self.contrato(f'REPARTO-SINTETICO-{n}', self.institucion)[0],
            date(2026, 9, 15), aplicar=True) for n in range(2))
        esperado = sum((i.aplicacion.interes_aplicado * Decimal('.375')).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            for i in ingresos)
        for _ in range(25):
            self.movimiento(self.solicitud, date(2026, 9, 30), monto=Decimal('10000'))
        for pagina in (1, 2):
            respuesta = self.consultar(institucion=self.institucion.pk, page=pagina)
            self.assertEqual(respuesta.context['reparto_periodo']['aprobado'], esperado)
            self.assertEqual(respuesta.context['interes_sin_convenio'], 0)

    def test_rechaza_convenio_solapado_invalido_y_edicion(self):
        convenio = self.crear_convenio()
        for datos in (
            {'version': 'SOLAPA', 'vigente_desde': date(2026, 9, 30), 'vigente_hasta': date(2026, 10, 1)},
            {'version': 'ATRAS', 'vigente_desde': date(2026, 10, 2), 'vigente_hasta': date(2026, 10, 1)},
            {'version': 'EXCESO', 'institucion': self.otra, 'porcentaje_aprobado': Decimal('100.0001')},
            {'version': 'SYNTHETIC-ONLY', 'vigente_desde': date(2026, 10, 1), 'vigente_hasta': date(2026, 10, 2)},
            {'version': ' ', 'institucion': self.otra},
        ):
            with self.subTest(datos=datos), self.assertRaises(ValidationError):
                self.crear_convenio(**datos)
        convenio.porcentaje_aprobado = Decimal('90')
        with self.assertRaises(ValidationError):
            convenio.save()

    def test_alta_en_admin_requiere_permiso_dedicado_y_registra_aprobador(self):
        usuario = get_user_model().objects.create_user('finanzas-sintetico', is_staff=True)
        self.client.force_login(usuario)
        url = reverse('admin:financiacion_educativa_conveniorepartointereseducativo_add')
        datos = {'institucion': self.institucion.pk, 'version': 'QA-ONLY', 'vigente_desde': '2026-09-01',
                 'vigente_hasta': '2026-09-30', 'porcentaje_aprobado': '52.1250', 'referencia_aprobacion': 'QA'}
        self.assertEqual(self.client.post(url, datos).status_code, 403)
        usuario.user_permissions.add(Permission.objects.get(codename='add_conveniorepartointereseducativo'))
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(ConvenioRepartoInteresEducativo.objects.get().aprobado_por, usuario)
        self.assertContains(self.client.post(url, {**datos, 'version': 'QA-OVERLAP'}), 'se solapa con la vigencia')

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
        self.assertNotContains(respuesta, 'class="collections-installment-shares"')
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


@skipUnless(connection.vendor == 'postgresql', 'Requiere conexiones independientes a PostgreSQL.')
@override_settings(**AISLAMIENTO)
class ConcurrenciaRepartoTableroTests(DatosTableroRecaudos, TransactionTestCase):
    def comprobar_aplicacion_intercalada(self, fecha_aplicacion):
        from financiacion_educativa.dashboards.operaciones import tablero_recaudos

        self.crear_convenio(vigente_hasta=date(2026, 10, 31))
        inicial = self.movimiento(self.solicitud, date(2026, 9, 1), aplicar=True)
        filtros = {'desde': '2026-09-01', 'hasta': '2026-10-31', 'institucion': self.institucion.pk}
        self.client.force_login(self.actor)
        construir = tablero_recaudos.construir_tablero

        def aplicar():
            try:
                with transaction.atomic():
                    ingreso = self.movimiento(self.solicitud, fecha_aplicacion, aplicar=True)
                    return ingreso.aplicacion.interes_aplicado
            finally:
                connections.close_all()

        intereses = [inicial.aplicacion.interes_aplicado]
        with ThreadPoolExecutor(max_workers=1) as escritor:
            def intercalar(*args, **kwargs):
                tablero = construir(*args, **kwargs)
                intereses.append(escritor.submit(aplicar).result(timeout=30))
                return tablero

            with patch.object(tablero_recaudos, 'construir_tablero', side_effect=intercalar):
                durante = self.client.get(self.ruta_tablero(), filtros)
        despues = self.client.get(self.ruta_tablero(), filtros)
        self.assertEqual(len(intereses), 2)
        for respuesta, fechas, importes in (
            (durante, [inicial.fecha_efectiva], intereses[:1]),
            (despues, [inicial.fecha_efectiva, fecha_aplicacion], intereses),
        ):
            self.assertEqual(respuesta.status_code, 200)
            grupos = {}
            for fecha, interes in zip(fechas, importes):
                grupos.setdefault(fecha.replace(day=1), []).append(interes)
            meses = respuesta.context['meses']
            self.assertEqual([mes['mes'] for mes in meses], sorted(grupos))
            aprobado_total = Decimal(0)
            for mes in meses:
                importes_mes = grupos[mes['mes']]
                aprobado = sum((i * Decimal('.375')).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                               for i in importes_mes)
                aprobado_total += aprobado
                self.assertEqual(mes['intereses'], sum(importes_mes))
                self.assertEqual(mes['reparto'], {'aprobado': aprobado, 'institucion': sum(importes_mes) - aprobado})
                self.assertEqual(mes['interes_sin_convenio'], 0)
            self.assertEqual(respuesta.context['totales']['intereses'], sum(importes))
            self.assertEqual(respuesta.context['reparto_periodo'],
                             {'aprobado': aprobado_total, 'institucion': sum(importes) - aprobado_total})

    def test_aplicacion_concurrente_en_mes_nuevo(self):
        self.comprobar_aplicacion_intercalada(date(2026, 10, 1))

    def test_aplicacion_concurrente_en_mes_existente(self):
        self.comprobar_aplicacion_intercalada(date(2026, 9, 30))
