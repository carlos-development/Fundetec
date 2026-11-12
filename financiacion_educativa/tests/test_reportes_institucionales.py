from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from financiacion_educativa.choices import EstadoSolicitudFinanciacion as Estado
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.test_portal_institucion import DatosPortalInstitucion
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from instituciones.models import MembresiaInstitucion
from instituciones.services.membresias import desactivar_membresia


class DatosReportesInstitucionales(DatosPortalInstitucion):
    curso = 'Curso Analisis & Datos'
    rango = {'desde': '2026-09-01', 'hasta': '2026-09-30'}

    def preparar_reportes(self):
        self.preparar_portal()
        self.radicar(self.autorizada, datetime(2026, 9, 1))
        self.radicar(self.sin_autorizacion, datetime(2026, 9, 30, 23, 59, 59, 999999))
        self.radicar(self.pendiente, datetime(2026, 9, 15, 12))
        self.radicar(self.ajena, datetime(2026, 9, 15, 12))
        self.rechazada = self.nueva('SYNTHETIC-REJECTED', Estado.REJECTED)
        self.cancelada = self.nueva('SYNTHETIC-CANCELLED', Estado.CANCELLED)
        self.segundo_curso, _, _, _ = self.contrato('SYNTHETIC-SECOND-COURSE')
        self.radicar(self.segundo_curso, datetime(2026, 9, 12, 12), curso='Curso Gestion')
        self.anterior, _, _, _ = self.contrato('SYNTHETIC-BEFORE-BOUNDARY')
        self.radicar(self.anterior, datetime(2026, 8, 31, 23, 59, 59, 999999))
        self.posterior = self.nueva('SYNTHETIC-AFTER-BOUNDARY', Estado.APPROVED)
        self.radicar(self.posterior, datetime(2026, 10, 1))
        self.exclusiva_ajena = self.nueva('SYNTHETIC-EXCLUSIVE-OTHER', Estado.PENDING_DOCUMENT,
            institucion=self.otra)
        self.radicar(self.exclusiva_ajena, datetime(2026, 9, 15), curso='Curso exclusivo ajeno')

    def nueva(self, referencia, estado, institucion=None):
        solicitud = crear_solicitud(institucion=institucion or self.institucion,
            referencia=referencia, correo=referencia.lower() + '@example.test')
        solicitud.estado = estado
        solicitud.save(update_fields=['estado'])
        self.radicar(solicitud, datetime(2026, 9, 20, 12))
        return solicitud

    def radicar(self, solicitud, instante, curso=None):
        # Synthetic auto_now_add dates only, never real or historical records.
        instante = instante.replace(tzinfo=ZoneInfo('America/Bogota'))
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            creada_en=instante, nombre_curso=curso or self.curso)


@override_settings(**{**AISLAMIENTO, 'TIME_ZONE': 'America/Bogota'})
class ReportesInstitucionalesTests(DatosReportesInstitucionales, TestCase):
    def setUp(self):
        self.preparar_reportes()
        self.client.force_login(self.usuario)

    def reporte(self, **filtros):
        respuesta = self.client.get(self.ruta('reportes'), {**self.rango, **filtros})
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def cifras(self, respuesta):
        return {c['codigo']: c['total'] for c in respuesta.context['reporte']['indicadores']}

    def verificar_enlaces(self, respuesta):
        reporte = respuesta.context['reporte']
        for cifra in [*reporte['indicadores'], *(c for p in reporte['periodos'] for c in p['cifras'])]:
            with self.subTest(url=cifra['url']):
                listado = self.client.get(cifra['url'])
                self.assertEqual(listado.status_code, 200)
                self.assertEqual(listado.context['pagina'].paginator.count, cifra['total'])
                for item in listado.context['pagina']:
                    self.assertNotIn(item['id'], (self.ajena.pk, self.exclusiva_ajena.pk))
                self.assertContains(listado, 'Volver a reportes')

    def test_totales_mensuales_y_listados_comparten_la_misma_cohorte(self):
        respuesta = self.reporte()
        self.assertEqual(self.cifras(respuesta), {'recibidas': 6, 'aprobadas': 3, 'autorizadas': 2, 'pendientes': 1})
        self.verificar_enlaces(respuesta)
        self.assertEqual(len(respuesta.context['reporte']['periodos']), 1)
        cifras = self.cifras(respuesta)
        self.assertEqual(cifras['aprobadas'], cifras['autorizadas'] + cifras['pendientes'])

    def test_recibidas_incluye_rechazadas_canceladas_y_tramites_una_sola_vez(self):
        cifra = self.reporte().context['reporte']['indicadores'][0]
        filas = self.client.get(cifra['url']).context['pagina']
        self.assertEqual({item['id'] for item in filas}, {self.autorizada.pk, self.sin_autorizacion.pk,
            self.pendiente.pk, self.rechazada.pk, self.cancelada.pk, self.segundo_curso.pk})
        self.assertContains(self.client.get(self.ruta('inicio')), '<span>Pendientes de iniciar</span>', html=True)

    def test_curso_filtra_todos_los_totales_y_sus_listados(self):
        respuesta = self.reporte(programa=self.curso)
        self.assertEqual(self.cifras(respuesta), {'recibidas': 5, 'aprobadas': 2, 'autorizadas': 1, 'pendientes': 1})
        self.verificar_enlaces(respuesta)
        for cifra in respuesta.context['reporte']['indicadores']:
            parametros = parse_qs(urlsplit(cifra['url']).query)
            self.assertEqual(parametros['programa'], [self.curso])

    def test_limites_inclusivos_y_zona_horaria_no_usan_fecha_de_firma(self):
        respuesta = self.reporte(programa=self.curso)
        self.assertEqual(self.cifras(respuesta)['autorizadas'], 1)
        aprobadas = next(c for c in respuesta.context['reporte']['indicadores'] if c['codigo'] == 'aprobadas')
        ids = {item['id'] for item in self.client.get(aprobadas['url']).context['pagina']}
        self.assertEqual(ids, {self.autorizada.pk, self.sin_autorizacion.pk})
        self.assertNotIn(self.anterior.pk, ids)
        self.assertNotIn(self.posterior.pk, ids)
        firma = self.autorizada.procesos_firma.get()
        self.assertGreater(firma.firmado_en.date(), date(2026, 9, 30))

    def test_un_dia_y_meses_parciales_recortan_los_enlaces(self):
        un_dia = self.reporte(desde='2026-09-30', hasta='2026-09-30')
        self.assertEqual(self.cifras(un_dia), {'recibidas': 1, 'aprobadas': 1, 'autorizadas': 0, 'pendientes': 1})
        self.verificar_enlaces(un_dia)
        parcial = self.reporte(desde='2026-09-15', hasta='2026-10-15')
        self.verificar_enlaces(parcial)
        for periodo in parcial.context['reporte']['periodos']:
            query = parse_qs(urlsplit(periodo['cifras'][0]['url']).query)
            self.assertEqual(query['desde'], ['2026-09-15' if periodo['mes'].month == 9 else '2026-10-01'])
            self.assertEqual(query['hasta'], ['2026-09-30' if periodo['mes'].month == 9 else '2026-10-15'])

    def test_varios_meses_suman_exactamente_los_indicadores(self):
        respuesta = self.reporte(desde='2026-08-01', hasta='2026-10-31')
        reporte = respuesta.context['reporte']
        self.assertEqual([p['mes'].month for p in reporte['periodos']], [8, 9, 10])
        for cifra in reporte['indicadores']:
            self.assertEqual(cifra['total'], sum(next(c['total'] for c in p['cifras']
                if c['codigo'] == cifra['codigo']) for p in reporte['periodos']))
        self.verificar_enlaces(respuesta)

    def test_dos_instituciones_no_comparten_cifras_opciones_o_detalles(self):
        propia = self.reporte(institucion_id=str(self.otra.pk))
        self.assertEqual(self.cifras(propia)['recibidas'], 6)
        self.assertNotContains(propia, 'Curso exclusivo ajeno')
        self.assertEqual(self.client.get(self.ruta('solicitud-detalle', self.ajena.pk)).status_code, 404)
        self.client.force_login(self.usuario_ajeno)
        ajena = self.reporte(institucion_id=str(self.institucion.pk))
        self.assertEqual(self.cifras(ajena), {'recibidas': 2, 'aprobadas': 1, 'autorizadas': 1, 'pendientes': 0})
        for cifra in ajena.context['reporte']['indicadores']:
            listado = self.client.get(cifra['url'])
            self.assertEqual(listado.context['pagina'].paginator.count, cifra['total'])
            self.assertNotIn(self.autorizada.pk, [item['id'] for item in listado.context['pagina']])
        self.assertEqual(self.client.get(self.ruta('solicitud-detalle', self.autorizada.pk)).status_code, 404)

    def test_curso_ajeno_fechas_invalidas_o_indicador_manipulado_fallan_cerrado(self):
        for cambios in ({'programa': 'Curso exclusivo ajeno'}, {'desde': ''}, {'desde': 'invalid'},
                        {'desde': '2026-10-01', 'hasta': '2026-09-01'}):
            respuesta = self.client.get(self.ruta('reportes'), {**self.rango, **cambios})
            self.assertEqual(respuesta.status_code, 400)
            self.assertIsNone(respuesta.context['reporte'])
            self.assertNotContains(respuesta, 'data-indicador=', status_code=400)
        invalida = self.client.get(self.ruta('solicitudes'), {**self.rango, 'indicador': 'forged'})
        self.assertEqual(invalida.status_code, 400)
        self.assertEqual(invalida.context['pagina'].paginator.count, 0)

    def test_vacio_muestra_ceros_y_cada_enlace_tiene_cero_filas(self):
        respuesta = self.reporte(desde='2030-01-01', hasta='2030-01-31')
        self.assertEqual(sum(self.cifras(respuesta).values()), 0)
        self.assertEqual(respuesta.context['reporte']['periodos'], [])
        self.assertContains(respuesta, 'Sin radicaciones en este rango')
        self.verificar_enlaces(respuesta)

    def test_rango_predeterminado_explicito_de_doce_meses(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 9, 30)):
            respuesta = self.client.get(self.ruta('reportes'))
        self.assertEqual(respuesta.context['reporte']['desde'], date(2025, 10, 1))
        self.assertEqual(respuesta.context['reporte']['hasta'], date(2026, 9, 30))
        self.verificar_enlaces(respuesta)

    def test_paginacion_del_respaldo_conserva_rango_curso_e_indicador(self):
        for indice in range(26):
            self.nueva(f'SYNTHETIC-PAGE-{indice}', Estado.PENDING_DOCUMENT)
        cifra = self.reporte(programa=self.curso).context['reporte']['indicadores'][0]
        primera = self.client.get(cifra['url'])
        segunda = self.client.get(cifra['url'] + '&page=2')
        self.assertEqual(primera.context['pagina'].paginator.count, cifra['total'])
        self.assertEqual(len(primera.context['pagina']) + len(segunda.context['pagina']), cifra['total'])
        for fragmento in ('indicador=recibidas', 'desde=2026-09-01', 'hasta=2026-09-30', 'programa=Curso'):
            self.assertIn(fragmento, primera.context['querystring'])

    def test_sandbox_no_fabrica_autorizacion_y_firma_no_vigente_pasa_a_pendiente(self):
        simular_resultado_sandbox(application_id=self.sin_autorizacion.pk, status='APPROVED', actor=self.actor)
        antes = self.cifras(self.reporte())
        self.assertEqual(antes['autorizadas'], 2)
        self.assertEqual(antes['pendientes'], 1)
        artefacto = self.autorizada.artefactos_contractuales.get()
        artefacto.vigente = False
        artefacto.save(update_fields=['vigente'])
        despues = self.reporte()
        self.assertEqual(self.cifras(despues)['autorizadas'], 1)
        self.assertEqual(self.cifras(despues)['pendientes'], 2)
        self.verificar_enlaces(despues)

    def test_roles_consulta_revocacion_y_post_preservan_permisos(self):
        for rol in MembresiaInstitucion.Rol.values:
            self.membresia.rol = rol
            self.membresia.save(update_fields=['rol'])
            self.assertEqual(self.reporte().status_code, 200)
        self.assertEqual(self.client.post(self.ruta('reportes')).status_code, 405)
        desactivar_membresia(membresia=self.membresia, actor=self.actor)
        self.assertEqual(self.client.get(self.ruta('reportes')).status_code, 403)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(self.ruta('reportes')).status_code, 403)

    def test_reporte_no_carga_personas_recaudos_diagnosticos_ni_hace_mutaciones(self):
        anterior = list(SolicitudFinanciacionEducativa.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.reporte()
        self.assertLessEqual(len(consultas), 12)
        self.assertFalse(any('recaudoeducativo' in c['sql'].lower() for c in consultas))
        for privado in ('ANA MARIA', 'example.test', 'cartera', 'operaciones/', 'test-only/', 'token_documento'):
            self.assertNotContains(respuesta, privado)
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(list(SolicitudFinanciacionEducativa.objects.order_by('pk').values()), anterior)
