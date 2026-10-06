from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo as ArtefactoEstado,
    EstadoProcesoFirmaEducativa as FirmaEstado,
    EstadoSolicitudFinanciacion as Estado,
    TipoArtefactoContractualEducativo as TipoArtefacto,
)
from financiacion_educativa.dashboards.operaciones.cartera import (
    expedientes_en_tramite, indicadores_obligaciones, obligaciones_autorizadas,
)
from financiacion_educativa.dashboards.operaciones.permissions import PERMISO_ACCESO, PERMISO_SOLICITUDES
from financiacion_educativa.models import (
    ArtefactoContractualEducativo, CondicionesFinancieras, ProcesoFirmaEducativa,
    CuotaAmortizacionEducativa, SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.reglas_financieras import crear_fotografia_condiciones_financieras
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests.factories import crear_configuracion_financiera, crear_institucion, crear_solicitud
from instituciones.models import MembresiaInstitucion


class DatosObligacionesMixin:
    def preparar_datos(self):
        self.actor = get_user_model().objects.create_superuser('cartera', 'cartera@example.test', 'test-only')
        self.institucion = crear_institucion('1')
        self.otra = crear_institucion('2')
        self.configuracion = crear_configuracion_financiera()
        self.url = reverse('financiacion_educativa_web:operaciones:obligaciones')

    def contrato(self, referencia='C-001', institucion=None, estado=Estado.APPROVED, version=1):
        solicitud = crear_solicitud(institucion=institucion or self.institucion, referencia=referencia)
        fotografia = crear_fotografia_condiciones_financieras(
            solicitud, fecha_inicio_plan=timezone.localdate(), configuracion=self.configuracion, bloquear=True,
        )
        instante = timezone.now()
        artefacto = ArtefactoContractualEducativo.objects.create(
            solicitud=solicitud, fotografia_financiera=fotografia,
            tipo=TipoArtefacto.PROMISSORY_NOTE, numero_version=version,
            estado=ArtefactoEstado.SIGNED, numero_documento=referencia,
            version_plantilla='test-only', archivo='test-only/contrato.pdf',
            hash_sha256='a' * 64, tamano_bytes=100,
            archivo_firmado='test-only/firmado.pdf', hash_firmado_sha256='b' * 64,
            tamano_firmado_bytes=120, firmado_en=instante,
        )
        proceso = ProcesoFirmaEducativa.objects.create(
            solicitud=solicitud, artefacto=artefacto, estado=FirmaEstado.SIGNED,
            external_id=f'test-{uuid4()}', destinatario_hmac='c' * 64, firmado_en=instante,
        )
        solicitud.estado = estado
        solicitud.save(update_fields=['estado'])
        return solicitud, fotografia, artefacto, proceso

    def detalle_url(self, solicitud):
        return reverse('financiacion_educativa_web:operaciones:obligacion-detalle', args=[solicitud.pk])


@override_settings(
    DEPLOYMENT_ENVIRONMENT='test', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_QA_MODE=False, EMAIL_LIVE_DELIVERY_ENABLED=False,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class DashboardObligacionesTests(DatosObligacionesMixin, TestCase):
    def setUp(self):
        self.preparar_datos()
        self.client.force_login(self.actor)

    def test_cero_obligaciones_no_aparenta_pagos_conciliados(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['indicadores_financieros']['capital'], Decimal('0'))
        self.assertContains(respuesta, 'No disponible', count=5)
        self.assertContains(respuesta, 'Sin obligaciones autorizadas')

    def test_tarjetas_listado_y_api_reconcilian(self):
        s1, f1, _, _ = self.contrato()
        _, f2, _, _ = self.contrato('C-002', self.otra)
        respuesta = self.client.get(self.url)
        indicadores = respuesta.context['indicadores_financieros']
        filas = respuesta.context['pagina'].object_list
        self.assertEqual(indicadores['cantidad'], len(filas))
        self.assertEqual(indicadores['capital'], sum((r['capital'] for r in filas), Decimal('0')))
        self.assertEqual(indicadores['solicitado'], f1.valor_financiado + f2.valor_financiado)
        self.assertEqual(obtener_resultado_publico(s1).condiciones_financieras['financed_amount'], format(f1.capital_financiado, '.2f'))
        self.assertTrue(obtener_resultado_publico(s1).curso_autorizado)
        inicio = self.client.get(reverse('financiacion_educativa_web:operaciones:inicio'))
        self.assertEqual(inicio.context['indicadores']['obligaciones'], indicadores)
        self.assertContains(inicio, 'Capital contractual financiado')

    def test_firma_pendiente_y_firmada_sin_autorizacion_no_cuentan(self):
        self.contrato('PENDING', estado=Estado.PENDING_SIGNATURE)
        s, _, _, p = self.contrato('NO-SIGNED')
        ProcesoFirmaEducativa.objects.filter(pk=p.pk).update(estado=FirmaEstado.SENT)
        self.assertFalse(obligaciones_autorizadas().exists())
        self.assertFalse(obtener_resultado_publico(s).curso_autorizado)

    def test_integridad_y_vigencia_fallan_cerradas(self):
        s, f, a, p = self.contrato()
        casos = [
            (CondicionesFinancieras, f.pk, {'activa': False}),
            (CondicionesFinancieras, f.pk, {'bloqueada': False}),
            (CondicionesFinancieras, f.pk, {'es_legado': True}),
            (ArtefactoContractualEducativo, a.pk, {'vigente': False}),
            (ArtefactoContractualEducativo, a.pk, {'estado': ArtefactoEstado.CANCELLED}),
            (ArtefactoContractualEducativo, a.pk, {'archivo_firmado': ''}),
            (ArtefactoContractualEducativo, a.pk, {'hash_firmado_sha256': ''}),
            (ArtefactoContractualEducativo, a.pk, {'tamano_firmado_bytes': None}),
            (ArtefactoContractualEducativo, a.pk, {'firmado_en': None}),
            (ProcesoFirmaEducativa, p.pk, {'firmado_en': None}),
        ]
        for modelo, pk, cambio in casos:
            original = modelo.objects.values(*cambio).get(pk=pk)
            with self.subTest(cambio=cambio):
                modelo.objects.filter(pk=pk).update(**cambio)
                self.assertFalse(obligaciones_autorizadas().exists())
                self.assertFalse(obtener_resultado_publico(s).curso_autorizado)
                self.assertEqual(self.client.get(self.detalle_url(s)).status_code, 404)
            modelo.objects.filter(pk=pk).update(**original)

    def test_relaciones_de_otra_solicitud_no_autorizan(self):
        s, f, a, p = self.contrato()
        otra, f2, _, _ = self.contrato('AJENA', self.otra)
        ArtefactoContractualEducativo.objects.filter(pk=a.pk).update(fotografia_financiera=f2)
        self.assertEqual(list(obligaciones_autorizadas().values_list('solicitud_id', flat=True)), [otra.pk])
        self.assertFalse(obtener_resultado_publico(s).curso_autorizado)
        ArtefactoContractualEducativo.objects.filter(pk=a.pk).update(fotografia_financiera=f)
        ProcesoFirmaEducativa.objects.filter(pk=p.pk).update(solicitud=otra)
        self.assertEqual(obligaciones_autorizadas().count(), 1)
        self.assertFalse(obtener_resultado_publico(s).curso_autorizado)
        ProcesoFirmaEducativa.objects.filter(pk=p.pk).update(solicitud=s)
        tercera = crear_solicitud(institucion=self.otra, referencia='TERCERA')
        ArtefactoContractualEducativo.objects.filter(pk=a.pk).update(solicitud=tercera)
        self.assertFalse(obtener_resultado_publico(s).curso_autorizado)

    def test_versiones_y_matricula_no_duplican_el_contrato(self):
        s, f, a, p = self.contrato()
        ArtefactoContractualEducativo.objects.create(
            solicitud=s, fotografia_financiera=f, tipo=TipoArtefacto.ENROLLMENT_FORM,
            numero_version=1, numero_documento='MATRICULA', version_plantilla='test',
            archivo='test-only/matricula.pdf', hash_sha256='d' * 64, tamano_bytes=100,
        )
        valores = CondicionesFinancieras.objects.values().get(pk=f.pk)
        valores.update(id=uuid4(), numero_version=2, activa=False, bloqueada=False)
        historica = CondicionesFinancieras.objects.create(**valores)
        anterior = ArtefactoContractualEducativo.objects.values().get(pk=a.pk)
        anterior.update(id=uuid4(), numero_version=2, vigente=False, fotografia_financiera_id=historica.pk)
        viejo = ArtefactoContractualEducativo.objects.create(**anterior)
        ProcesoFirmaEducativa.objects.create(
            solicitud=s, artefacto=viejo, estado=FirmaEstado.SIGNED,
            external_id=f'test-{uuid4()}', destinatario_hmac='e' * 64, firmado_en=p.firmado_en,
        )
        indicadores = indicadores_obligaciones(obligaciones_autorizadas())
        self.assertEqual(indicadores['cantidad'], 1)
        self.assertEqual(indicadores['capital'], f.capital_financiado)
        self.assertEqual(self.client.get(self.detalle_url(s)).context['fotografia'].pk, f.pk)

    def test_pagare_sustituido_no_cuenta_la_firma_anterior(self):
        s, f, a, _ = self.contrato()
        ArtefactoContractualEducativo.objects.filter(pk=a.pk).update(vigente=False)
        ArtefactoContractualEducativo.objects.create(
            solicitud=s, fotografia_financiera=f, tipo=TipoArtefacto.PROMISSORY_NOTE,
            numero_version=2, numero_documento='NUEVO', version_plantilla='test',
            archivo='test-only/nuevo.pdf', hash_sha256='d' * 64, tamano_bytes=100,
        )
        self.assertFalse(obligaciones_autorizadas().exists())

    def test_simulacion_no_es_obligacion_ni_tramite(self):
        real, _, _, _ = self.contrato()
        simular_resultado_sandbox(application_id=real.pk, actor=self.actor, status='APPROVED')
        pendiente = crear_solicitud(institucion=self.institucion, referencia='SIM-PENDING')
        simular_resultado_sandbox(application_id=pendiente.pk, actor=self.actor, status='APPROVED')
        self.assertFalse(obligaciones_autorizadas().exists())
        self.assertFalse(expedientes_en_tramite(filtros={}).exists())
        self.assertTrue(obtener_resultado_publico(pendiente).curso_autorizado)

    def test_fotografias_en_tramite_se_separan_de_obligaciones(self):
        self.contrato('PENDING', estado=Estado.PENDING_SIGNATURE)
        self.contrato('APPROVED')
        respuesta = self.client.get(self.url, {'ambito': 'tramite'})
        self.assertEqual(respuesta.context['indicadores_financieros']['cantidad'], 1)
        self.assertContains(respuesta, 'PENDING')
        self.assertNotContains(respuesta, '<strong>APPROVED</strong>')
        self.assertContains(respuesta, 'no es cartera autorizada')

    def test_filtros_institucion_estado_y_fechas_inclusivas(self):
        s, _, _, _ = self.contrato()
        self.contrato('OTRA', self.otra)
        hoy = timezone.localdate().isoformat()
        respuesta = self.client.get(self.url, {'institucion': self.institucion.pk, 'estado': 'APPROVED', 'desde': hoy, 'hasta': hoy})
        self.assertEqual(respuesta.context['indicadores_financieros']['cantidad'], 1)
        self.assertEqual(respuesta.context['pagina'].object_list[0]['id'], s.pk)
        self.assertNotContains(respuesta, '<strong>OTRA</strong>')
        self.assertEqual(self.client.get(self.url, {'estado': 'UNDER_REVIEW'}).context['indicadores_financieros']['cantidad'], 0)
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(self.url, {'desde': manana}).context['indicadores_financieros']['cantidad'], 0)

    def test_fecha_autorizacion_no_es_fecha_creacion(self):
        s, _, _, _ = self.contrato()
        SolicitudFinanciacionEducativa.objects.filter(pk=s.pk).update(creada_en=timezone.now() - timedelta(days=100))
        hoy = timezone.localdate().isoformat()
        self.assertEqual(self.client.get(self.url, {'desde': hoy}).context['indicadores_financieros']['cantidad'], 1)
        pendiente = crear_solicitud(institucion=self.institucion, referencia='OLD-PENDING')
        SolicitudFinanciacionEducativa.objects.filter(pk=pendiente.pk).update(creada_en=timezone.now() - timedelta(days=100))
        self.assertEqual(self.client.get(self.url, {'ambito': 'tramite', 'desde': hoy}).context['indicadores_financieros']['cantidad'], 0)

    def test_filtros_invalidos_fallan_sin_filtrar_registros(self):
        self.contrato()
        for filtros in ({'institucion': uuid4()}, {'estado': 'INVALID'}, {'ambito': 'INVALID'},
                        {'desde': 'invalid'}, {'desde': '2026-10-07', 'hasta': '2026-10-06'}, {'page': 0}):
            with self.subTest(filtros=filtros):
                respuesta = self.client.get(self.url, filtros)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.context['indicadores_financieros']['cantidad'], 0)

    def test_detalle_calendario_cargos_sin_recalculo_o_escrituras(self):
        s, f, _, _ = self.contrato()
        with CaptureQueriesContext(connection) as consultas, patch(
            'financiacion_educativa.services.reglas_financieras.calcular_condiciones_financieras',
            side_effect=AssertionError('No recalcular contratos al consultar'),
        ):
            respuesta = self.client.get(self.detalle_url(s))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in consultas))
        self.assertEqual(list(respuesta.context['cuotas'].values_list('pk', flat=True)), list(f.cuotas.values_list('pk', flat=True)))
        for texto in ('Originaci&oacute;n', 'IVA de originaci&oacute;n', 'Figarantias', 'SURA', 'Saldo final programado', 'No disponible'):
            self.assertContains(respuesta, texto)
        self.assertNotContains(respuesta, 'test-only/firmado.pdf')
        self.assertNotContains(respuesta, s.correo)

    def test_proximos_vencimientos_solo_calendario_y_limites_30_dias(self):
        _, f, _, _ = self.contrato()
        hoy = date(2026, 10, 6)
        f.cuotas.all().delete()
        for numero, dias in enumerate((-1, 0, 30, 31), 1):
            CuotaAmortizacionEducativa.objects.create(
                fotografia=f, numero=numero, fecha_vencimiento=hoy + timedelta(days=dias),
                saldo_inicial=Decimal('400'), interes=Decimal('10'), capital=Decimal('90'),
                valor_cuota=Decimal('100'), saldo_final=Decimal('310'),
            )
        with patch('financiacion_educativa.dashboards.operaciones.cartera.timezone.localdate', return_value=hoy):
            consulta = obligaciones_autorizadas()
            self.assertEqual(consulta.get().proximo_vencimiento, hoy)
            self.assertEqual(indicadores_obligaciones(consulta)['vencimientos_30_dias'], 2)
            self.assertEqual(indicadores_obligaciones(consulta)['cuotas_30_dias'], Decimal('200'))

    def test_permisos_y_membresia_no_conceden_acceso_operativo(self):
        s, _, _, _ = self.contrato()
        usuario = get_user_model().objects.create_user('solo-institucion', is_staff=True)
        MembresiaInstitucion.objects.create(usuario=usuario, institucion=self.institucion, rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN)
        for url in (self.url, self.detalle_url(s)):
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, 403)
        usuario.user_permissions.add(Permission.objects.get(codename=PERMISO_ACCESO.split('.')[1]))
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        usuario.user_permissions.add(Permission.objects.get(codename=PERMISO_SOLICITUDES.split('.')[1]))
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(self.detalle_url(s)).status_code, 200)

    def test_get_only_no_store_y_detalle_inexistente(self):
        s, _, _, _ = self.contrato()
        for url in (self.url, self.detalle_url(s)):
            self.assertIn('no-store', self.client.get(url).headers['Cache-Control'])
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:obligacion-detalle', args=[uuid4()])).status_code, 404)

    def test_paginacion_conserva_filtros_y_totales(self):
        # Veintiseis fotografias de solicitudes distintas: una fila por obligacion.
        for numero in range(26):
            self.contrato(f'PAGE-{numero:02}')
        respuesta = self.client.get(self.url, {'institucion': self.institucion.pk, 'estado': 'APPROVED'})
        self.assertEqual(respuesta.context['indicadores_financieros']['cantidad'], 26)
        self.assertEqual(len(respuesta.context['pagina'].object_list), 25)
        self.assertContains(respuesta, f'institucion={self.institucion.pk}&amp;estado=APPROVED&amp;page=2')
        segunda = self.client.get(self.url, {'institucion': self.institucion.pk, 'estado': 'APPROVED', 'page': 2})
        self.assertEqual(len(segunda.context['pagina'].object_list), 1)
        self.assertEqual(segunda.context['indicadores_financieros'], respuesta.context['indicadores_financieros'])

    def test_consultas_listado_no_crecen_por_obligacion(self):
        self.contrato()
        with CaptureQueriesContext(connection) as uno:
            self.client.get(self.url)
        for numero in range(4):
            self.contrato(f'QUERY-{numero}')
        with CaptureQueriesContext(connection) as varios:
            self.client.get(self.url)
        self.assertEqual(len(uno), len(varios))
