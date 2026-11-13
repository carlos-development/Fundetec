from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from instituciones.models import MembresiaInstitucion
from financiacion_educativa.models import (
    ArtefactoContractualEducativo, CondicionesFinancieras, CuotaAmortizacionEducativa,
    OutboxCorreoEducativo, ProcesoFirmaEducativa,
)
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo, ReporteRecaudoEducativo
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


HOY = date(2026, 11, 12)
AISLAR = {**AISLAMIENTO, 'TIME_ZONE': 'America/Bogota',
    'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': False}


class DatosSeguimiento(DatosIngresoOperaciones):
    def setUp(self):
        with patch('django.utils.timezone.localdate', return_value=HOY - timedelta(days=100)):
            super().setUp()
        reloj = patch('django.utils.timezone.localdate', return_value=HOY)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.datos['fecha'] = HOY

    def ruta(self, nombre='seguimiento-cartera', *args):
        return reverse('financiacion_educativa_web:operaciones:' + nombre, args=args)

    def contrato_fecha(self, referencia, primer_vencimiento, institucion=None):
        inicio = primer_vencimiento - relativedelta(months=1)
        with patch('django.utils.timezone.localdate', return_value=inicio):
            return self.contrato(referencia, institucion)

    def ingreso(self, solicitud=None, **datos):
        return self.registrar(solicitud_id=(solicitud or self.solicitud).pk,
            referencia='SYNTHETIC-' + uuid4().hex, clave=uuid4(), **datos)[0]

    def aplicar(self, ingreso):
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)

    def datos_visuales(self):
        self.ingreso_abierto = self.ingreso(monto=Decimal('125000.00'))
        self.reporte_abierto, _ = self.reportar()
        self.solicitud_ajena, self.foto_ajena, _, _ = self.contrato_fecha('SINTETICO-OTRA-INSTITUCION', HOY - timedelta(days=40), self.otra)
        self.solicitud_ajena.usuario, self.solicitud_ajena.correo = self.pagador, self.pagador.email
        self.solicitud_ajena.save(update_fields=['usuario', 'correo'])
        self.reporte_rechazado, _ = self.reportar(solicitud_id=self.solicitud_ajena.pk)
        self.escanear(self.reporte_rechazado.comprobante_id)
        recaudos.rechazar_reporte(reporte_id=self.reporte_rechazado.pk, actor=self.actor, motivo='Soporte sintetico no conciliable')
        self.contrato_fecha('SINTETICO-VENCE-HOY', HOY)
        self.contrato_fecha('SINTETICO-MANANA', HOY + timedelta(days=1))


@override_settings(**AISLAR)
class SeguimientoCarteraTests(DatosSeguimiento, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.actor)

    def consultar(self, **parametros):
        return self.client.get(self.ruta(), parametros)

    def test_vencidas_importes_y_obligaciones_no_son_saldos(self):
        respuesta = self.consultar()
        self.assertEqual(respuesta.status_code, 200)
        vencidas = self.fotografia.cuotas.filter(fecha_vencimiento__lt=HOY)
        total = respuesta.context['totales']
        self.assertEqual(total['cuotas'], vencidas.count())
        self.assertEqual(total['obligaciones'], 1)
        self.assertEqual(total['importe'], sum(c.valor_cuota for c in vencidas))
        self.assertContains(respuesta, 'Importe contractual')
        self.assertEqual([f['fecha'] for f in respuesta.context['filas']], sorted(c.fecha_vencimiento for c in vencidas))

    def test_primera_cuota_es_numero_uno_no_primera_pendiente(self):
        ingreso = self.ingreso()
        self.aplicar(ingreso)
        primeras = self.consultar(bandeja='primeras')
        self.assertEqual(primeras.context['totales']['cuotas'], 0)
        vencidas = self.consultar()
        self.assertEqual(vencidas.context['filas'][0]['numero'], 2)
        self.assertEqual(vencidas.context['filas'][0]['primera_pendiente'], 2)

    def test_ayer_hoy_manana_y_rangos_rapidos_inclusivos(self):
        esperados = {}
        for delta in (-1, 0, 1, 7, 8, 30, 31):
            solicitud, _, _, _ = self.contrato_fecha('DIA-' + str(delta), HOY + timedelta(days=delta), self.otra)
            esperados[delta] = solicitud
        hoy = self.consultar(bandeja='hoy', institucion=self.otra.pk)
        self.assertEqual([f['solicitud_id'] for f in hoy.context['filas']], [esperados[0].pk])
        for rapido, dias in zip(hoy.context['rapidos'], (0, 7, 30)):
            pagina = self.client.get(rapido['url'])
            fechas = [f['fecha'] for f in pagina.context['filas']]
            self.assertTrue(all(HOY <= fecha <= HOY + timedelta(days=dias) for fecha in fechas))
            if dias:
                self.assertNotIn(HOY, fechas)
        vencidas = self.consultar(institucion=self.otra.pk)
        self.assertEqual([f['solicitud_id'] for f in vencidas.context['filas']], [esperados[-1].pk])

    def test_fecha_local_bogota_no_utc(self):
        from financiacion_educativa.services.seguimiento_cartera import etiqueta_vencimiento
        with patch('django.utils.timezone.now', return_value=datetime(2026, 11, 13, 2, 0, tzinfo=dt_timezone.utc)):
            # Pass an explicit timezone to bypass this fixture's frozen localdate.
            hoy_local = timezone.localtime(timezone.now()).date()
            self.assertEqual(hoy_local, HOY)
            self.assertEqual(etiqueta_vencimiento(HOY, hoy_local), 'Vence hoy')
            self.assertEqual(etiqueta_vencimiento(HOY - timedelta(days=1), hoy_local), 'Vencida')

    def test_prioridad_ingreso_conserva_segunda_senal_sin_duplicar(self):
        self.ingreso(monto=Decimal('50000'))
        self.reportar()
        respuesta = self.consultar(vista='obligaciones')
        fila = respuesta.context['filas'][0]
        self.assertEqual(fila['estado_recaudo'], 'INCOME')
        self.assertEqual(fila['ingresos_abiertos'], 1)
        self.assertEqual(fila['reportes_abiertos'], 1)
        self.assertEqual(fila['importe_ingresos'], Decimal('50000'))
        self.assertGreater(fila['cantidad'], 1)
        self.assertEqual(respuesta.context['senales']['ingresos'], 1)
        self.assertEqual(respuesta.context['senales']['importe'], Decimal('50000'))
        detalle = self.client.get(fila['url'])
        self.assertEqual(len(detalle.context['abiertos']), 2)
        self.assertTrue(detalle.context['abiertos'][0]['revision'])
        self.assertContains(detalle, 'Requiere revisi&oacute;n')
        self.assertEqual(detalle.context['accion'], 'Ver recaudos abiertos')

    def test_ingreso_revisable_y_reporte_son_del_contrato_no_de_cada_cuota(self):
        ingreso = self.ingreso(monto=Decimal('125000.00'))
        self.reportar()
        for vista in ('cuotas', 'obligaciones'):
            respuesta = self.consultar(vista=vista)
            self.assertEqual(respuesta.context['senales']['ingresos'], 1)
            self.assertEqual(respuesta.context['senales']['reportes'], 1)
            self.assertEqual(respuesta.context['senales']['importe'], ingreso.monto_recibido)
            self.assertGreater(respuesta.context['totales']['cuotas'], 1)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_aislamiento_de_filtros_y_referencia_en_dos_instituciones(self):
        self.datos_visuales()
        propia = self.consultar(institucion=self.institucion.pk)
        ajena = self.consultar(institucion=self.otra.pk)
        self.assertTrue(all(f['solicitud_id'] == self.solicitud.pk for f in propia.context['filas']))
        self.assertTrue(all(f['solicitud_id'] == self.solicitud_ajena.pk for f in ajena.context['filas']))
        self.assertEqual(ajena.context['senales']['ingresos'], 0)
        self.assertEqual(ajena.context['senales']['reportes'], 0)
        self.assertEqual(self.consultar(institucion=self.otra.pk, q=self.solicitud.referencia_externa).context['totales']['cuotas'], 0)

    def test_reporte_pendiente_rechazado_y_aplicado_se_distinguen(self):
        reporte, _ = self.reportar()
        self.assertEqual(self.consultar().context['filas'][0]['estado_recaudo'], 'REPORT')
        recaudos.rechazar_reporte(reporte_id=reporte.pk, actor=self.actor, motivo='Sintetico')
        self.assertEqual(self.consultar().context['filas'][0]['estado_recaudo'], 'NONE')
        ingreso = self.ingreso()
        self.assertEqual(self.consultar().context['filas'][0]['estado_recaudo'], 'INCOME')
        self.aplicar(ingreso)
        self.assertEqual(self.consultar().context['filas'][0]['estado_recaudo'], 'NONE')

    def test_otras_versiones_no_crean_senal(self):
        ingreso = self.ingreso(monto=Decimal('50000'))
        CondicionesFinancieras.objects.filter(pk=self.fotografia.pk).update(activa=False)
        datos = {campo.name: getattr(self.fotografia, campo.name) for campo in CondicionesFinancieras._meta.fields
            if campo.name not in ('id', 'numero_version', 'solicitud', 'configuracion')}
        otra_foto = CondicionesFinancieras.objects.create(solicitud=self.solicitud, configuracion=self.configuracion,
            numero_version=2, **datos)
        for cuota in self.fotografia.cuotas.order_by('numero'):
            CuotaAmortizacionEducativa.objects.create(fotografia=otra_foto,
                **{campo.name: getattr(cuota, campo.name) for campo in CuotaAmortizacionEducativa._meta.fields
                   if campo.name not in ('id', 'fotografia')})
        artefacto = ArtefactoContractualEducativo.objects.get(solicitud=self.solicitud)
        ArtefactoContractualEducativo.objects.filter(pk=artefacto.pk).update(fotografia_financiera=otra_foto)
        self.assertEqual(self.consultar().context['filas'][0]['estado_recaudo'], 'NONE')
        self.assertEqual(self.consultar().context['senales']['ingresos'], 0)
        self.assertTrue(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk).exists())

    def test_finalidad_o_importe_ambiguos_requieren_revision_no_aplicacion(self):
        self.ingreso(proposito='PRINCIPAL')
        detalle = self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk))
        self.assertEqual(detalle.context['accion'], 'Ver ingreso por aplicar')
        self.assertTrue(detalle.context['abiertos'][0]['revision'])
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_aplicados_importe_capital_interes_fecha_bancaria(self):
        ingreso = self.ingreso(fecha=HOY - timedelta(days=5))
        aplicacion = self.aplicar(ingreso)
        respuesta = self.consultar(bandeja='aplicados', desde=self.cuota.fecha_vencimiento, hasta=self.cuota.fecha_vencimiento)
        self.assertEqual(respuesta.context['totales']['cuotas'], 1)
        fila = respuesta.context['filas'][0]
        self.assertEqual(fila['importe'], aplicacion.capital_aplicado + aplicacion.interes_aplicado)
        self.assertEqual(fila['capital'], aplicacion.capital_aplicado)
        self.assertEqual(fila['intereses'], aplicacion.interes_aplicado)
        self.assertEqual(fila['fecha_bancaria'], ingreso.fecha_efectiva)
        self.assertNotEqual(fila['fecha'], fila['fecha_bancaria'])
        agrupada = self.consultar(bandeja='aplicados', vista='obligaciones')
        self.assertEqual(agrupada.context['filas'][0]['importe'], fila['importe'])

    def test_indicadores_y_listados_reconcilian_con_filtros(self):
        self.datos_visuales()
        for institucion in (None, self.institucion.pk, self.otra.pk):
            for estado in ('', 'INCOME', 'REPORT', 'NONE'):
                datos = {'estado_operativo': estado, 'desde': '2026-08-01', 'hasta': '2026-12-31'}
                if institucion:
                    datos['institucion'] = institucion
                pagina = self.consultar(**datos)
                for indicador in pagina.context['indicadores']:
                    respaldo = self.client.get(indicador['url'])
                    for campo in ('cuotas', 'obligaciones', 'importe'):
                        self.assertEqual(indicador[campo], respaldo.context['totales'][campo])
                    self.assertEqual(respaldo.context['pagina'].paginator.count, indicador['cuotas'])

    def test_version_autorizacion_y_sandbox_excluyen_approved_aislado(self):
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=self.solicitud)
        ProcesoFirmaEducativa.objects.filter(pk=proceso.pk).update(estado='SENT')
        self.assertEqual(self.consultar().context['totales']['cuotas'], 0)
        self.assertEqual(self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk)).status_code, 404)
        ProcesoFirmaEducativa.objects.filter(pk=proceso.pk).update(estado='SIGNED')
        simular_resultado_sandbox(application_id=self.solicitud.pk, actor=self.actor, status='APPROVED')
        self.assertEqual(self.consultar().context['totales']['cuotas'], 0)

    def test_permiso_directo_institucion_estudiante_y_staff_no_bastan(self):
        for usuario in (self.pagador, self.operador(()), self.operador((aplicacion_cuotas.PERMISO_ACCESO,))):
            usuario.is_staff = True
            usuario.save(update_fields=['is_staff'])
            MembresiaInstitucion.objects.create(usuario=usuario, institucion=self.institucion, rol='INSTITUTION_ADMIN')
            self.client.force_login(usuario)
            self.assertEqual(self.consultar().status_code, 403)
            self.assertEqual(self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk)).status_code, 403)
        self.client.force_login(self.operador((aplicacion_cuotas.PERMISO_ACCESO, recaudos.PERMISO_CONSULTAR,
            'financiacion_educativa.consultar_solicitudes_operativas')))
        self.assertEqual(self.consultar().status_code, 200)

    def test_estado_vacio_y_filtros_invalidos_fail_closed(self):
        self.assertContains(self.consultar(desde='2030-01-01'), 'Sin cuotas en esta selecci&oacute;n')
        for datos in ({'desde': 'bad'}, {'desde': '2027-01-01', 'hasta': '2020-01-01'},
                      {'institucion': uuid4()}, {'estado_operativo': 'PAID'}, {'bandeja': 'hack'}, {'vista': 'hack'}):
            with self.subTest(datos=datos):
                respuesta = self.consultar(**datos)
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotIn('totales', respuesta.context)

    def test_sin_escrituras_financieras_avisos_ni_cambios_por_get(self):
        self.datos_visuales()
        modelos = (CondicionesFinancieras, CuotaAmortizacionEducativa, AplicacionCuotaEducativa,
            ConciliacionRecaudoEducativo, ReporteRecaudoEducativo, OutboxCorreoEducativo, ProcesoFirmaEducativa)
        original = [list(m.objects.order_by('pk').values()) for m in modelos]
        self.consultar()
        self.consultar(vista='obligaciones')
        self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk))
        self.assertEqual(original, [list(m.objects.order_by('pk').values()) for m in modelos])

    def test_query_count_no_crece_por_fila(self):
        self.consultar()  # Warm permission/content-type caches.
        with CaptureQueriesContext(connection) as pocas:
            self.consultar()
        for i in range(6):
            self.contrato_fecha('EXTRA-' + str(i), HOY - timedelta(days=30), self.otra)
        with CaptureQueriesContext(connection) as muchas:
            self.consultar()
        self.assertEqual(len(pocas), len(muchas))
        with CaptureQueriesContext(connection) as agrupadas:
            self.consultar(vista='obligaciones')
        self.assertLessEqual(len(agrupadas), len(muchas) + 1)

    def test_paginacion_y_regreso_conservan_filtros_sin_open_redirect(self):
        for i in range(25):
            self.contrato_fecha('PAGE-' + str(i), HOY - timedelta(days=1), self.otra)
        datos = dict(institucion=self.otra.pk, q='PAGE-', estado_operativo='NONE', page=2)
        pagina = self.consultar(**datos)
        self.assertEqual(pagina.context['pagina'].paginator.count, 25)
        # Include the base obligation to exercise a second page with 28 rows.
        pagina = self.consultar(estado_operativo='NONE', page=2)
        self.assertGreater(pagina.context['totales']['cuotas'], len(pagina.context['filas']))
        detalle = self.client.get(pagina.context['filas'][0]['url'])
        query = parse_qs(urlsplit(detalle.context['vuelta']).query)
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(query['estado_operativo'], ['NONE'])
        contrato = self.client.get(detalle.context['url_obligacion'])
        self.assertContains(contrato, 'Volver al seguimiento filtrado')
        malicioso = self.client.get(self.ruta('obligacion-detalle', self.solicitud.pk),
            {'origen': 'cartera', 'next': 'https://evil.example', 'q': '<script>'})
        self.assertNotIn('evil.example', malicioso.context['seguimiento_regreso'])

    def test_solo_get_cache_y_filtro_de_referencia(self):
        self.assertEqual(self.client.post(self.ruta()).status_code, 405)
        self.assertEqual(self.consultar(q='NO-MATCH').context['totales']['cuotas'], 0)
        self.assertIn('no-store', self.consultar()['Cache-Control'])
        self.assertIn('no-store', self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk))['Cache-Control'])
        self.assertEqual(self.client.post(self.ruta('seguimiento-detalle', self.solicitud.pk)).status_code, 405)

    def test_filtros_de_vencimiento_son_inclusivos_sin_simular_corte_historico(self):
        vencimiento = self.cuota.fecha_vencimiento
        self.assertEqual(self.consultar(desde=vencimiento, hasta=vencimiento).context['totales']['cuotas'], 1)
        self.aplicar(self.ingreso(fecha=HOY))
        self.assertEqual(self.consultar(desde=vencimiento, hasta=vencimiento).context['totales']['cuotas'], 0)
        self.assertEqual(self.consultar(bandeja='aplicados', desde=vencimiento, hasta=vencimiento).context['totales']['cuotas'], 1)

    def test_regreso_de_recaudo_conserva_todos_los_filtros(self):
        self.ingreso()
        datos = {'institucion': str(self.institucion.pk), 'q': 'REC-', 'estado_operativo': 'INCOME',
            'desde': '2026-08-01', 'hasta': '2026-11-11', 'bandeja': 'vencidas', 'vista': 'obligaciones', 'page': '2'}
        detalle = self.client.get(self.ruta('seguimiento-detalle', self.solicitud.pk), datos)
        recaudo = self.client.get(detalle.context['url_accion'])
        self.assertEqual(recaudo.status_code, 200)
        self.assertEqual(parse_qs(urlsplit(recaudo.context['seguimiento_regreso']).query), {k: [v] for k, v in datos.items()})

    def test_portal_etiquetas_reporte_abierto_historial_y_plan_conservados(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk])
        self.assertContains(self.client.get(url), 'Vencida')
        self.reportar()
        respuesta = self.client.get(url)
        self.assertContains(respuesta, 'Reporte en revisi&oacute;n')
        self.assertNotContains(respuesta, 'Pagar y reportar transferencia')
        self.assertContains(respuesta, 'Descargar plan contractual en PDF')
        self.assertContains(respuesta, 'Historial de pagos y transferencias')
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_portal_hoy_y_manana_etiquetados_separadamente(self):
        for delta, etiqueta in ((0, 'Vence hoy'), (1, 'Pr\u00f3ximo vencimiento')):
            solicitud, _, _, _ = self.contrato_fecha('PORTAL-' + str(delta), HOY + timedelta(days=delta))
            solicitud.usuario, solicitud.correo = self.pagador, self.pagador.email
            solicitud.save(update_fields=['usuario', 'correo'])
            self.client.force_login(self.pagador)
            respuesta = self.client.get(reverse('financiacion_educativa_web:finanzas', args=[solicitud.pk]))
            self.assertContains(respuesta, etiqueta)
