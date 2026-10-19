from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from instituciones.models import Institucion
from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion as Estado, RolParticipante,
    TipoDocumentoFinanciacion as Tipo, TipoDocumentoIdentidad,
    TipoDecisionRevisionEducativa, MotivoDecisionRevisionEducativa,
)
from financiacion_educativa.models import (
    DecisionRevisionEducativa, ProcesoFirmaEducativa, CondicionesFinancieras,
    ArtefactoContractualEducativo,
)
from financiacion_educativa.services.politica_documental import (
    construir_politica_documental, requisito_listo_para_aprobacion,
    validar_expediente_para_aprobacion, soporte_ingresos_obligatorio,
    resumir_politica_documental,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales, completar_fase_documental,
)
from financiacion_educativa.services.orquestacion_automatica import ejecutar_orquestacion_automatica
from financiacion_educativa.services.cola_automatizacion import procesar_siguiente_trabajo
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.progreso_publico import obtener_progreso_publico
from financiacion_educativa.services.revision import decidir_solicitud
from financiacion_educativa.web.forms import DocumentoFinanciacionForm
from financiacion_educativa.tests import test_orquestacion_automatica as fixtures
from financiacion_educativa.tests.factories import crear_institucion


@override_settings(**fixtures.OrquestacionAutomaticaTests._overridden_settings)
class IngresosPorInstitucionTests(TestCase):
    setUp = fixtures.OrquestacionAutomaticaTests.setUp
    _solicitud_base = fixtures.OrquestacionAutomaticaTests._solicitud_base
    _participante = fixtures.OrquestacionAutomaticaTests._participante
    _documento = fixtures.OrquestacionAutomaticaTests._documento
    _firmar = fixtures.OrquestacionAutomaticaTests._firmar

    def expediente(self, referencia='INGRESOS-OPCIONAL', *, opcional=True, menor=False, lados=('frente', 'reverso')):
        solicitud = self._solicitud_base(referencia)
        institucion = solicitud.institucion
        institucion.requiere_soporte_ingresos = not opcional
        institucion.save(update_fields=['requiere_soporte_ingresos'])
        estudiante = self._participante(solicitud, menor=menor)
        for lado, tipo in [('frente', Tipo.STUDENT_ID_FRONT), ('reverso', Tipo.STUDENT_ID_BACK)]:
            if lado in lados:
                self._documento(solicitud, estudiante, tipo, f'{referencia}-{lado}')
        tutor = None
        if menor:
            tutor = self._participante(solicitud, tutor=True)
            for lado, tipo in [('frente', Tipo.GUARDIAN_ID_FRONT), ('reverso', Tipo.GUARDIAN_ID_BACK)]:
                self._documento(solicitud, tutor, tipo, f'{referencia}-tutor-{lado}')
        return solicitud, estudiante, tutor

    def test_predeterminado_obligatorio_y_ausencia_bloquea(self):
        self.assertTrue(Institucion._meta.get_field('requiere_soporte_ingresos').default)
        solicitud, _, _ = self.expediente(opcional=False)
        ingreso = next(r for r in construir_politica_documental(solicitud) if r.codigo == 'INCOME_CERTIFICATE')
        self.assertTrue(ingreso.obligatorio)
        with self.assertRaises(ValidationError):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        with self.assertRaises(ValidationError):
            validar_expediente_para_aprobacion(solicitud)

    def test_ausencia_opcional_envia_y_cola_continua_hasta_firma(self):
        solicitud, _, _ = self.expediente()
        solicitud = completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        enviado = solicitud.historial_estados.filter(estado_nuevo=Estado.PENDING_MANUAL_REVIEW).get()
        self.assertFalse(enviado.metadata['politica_documental']['soporte_ingresos_obligatorio'])
        for _ in range(7):
            procesar_siguiente_trabajo()
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, Estado.PENDING_SIGNATURE)
        self.assertFalse(solicitud.documentos.filter(tipo=Tipo.INCOME_CERTIFICATE).exists())
        self.assertEqual(ArtefactoContractualEducativo.objects.filter(solicitud=solicitud, vigente=True).count(), 2)
        self.assertIsNone(obtener_resultado_publico(solicitud).condiciones_financieras)
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        self.assertEqual(self._firmar(proceso).status_code, 200)
        solicitud.refresh_from_db()
        self.assertTrue(obtener_resultado_publico(solicitud).curso_autorizado)

    def test_frente_de_cedula_sigue_obligatorio(self):
        solicitud, _, _ = self.expediente(lados=('reverso',))
        with self.assertRaises(ValidationError):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)

    def test_reverso_de_cedula_sigue_obligatorio(self):
        solicitud, _, _ = self.expediente(lados=('frente',))
        with self.assertRaises(ValidationError):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)

    def test_pasaporte_conserva_excepcion_de_una_cara(self):
        solicitud, estudiante, _ = self.expediente(lados=('frente',))
        estudiante.tipo_documento = TipoDocumentoIdentidad.PASSPORT
        estudiante.save(update_fields=['tipo_documento'])
        codigos = {r.codigo for r in construir_politica_documental(solicitud)}
        self.assertNotIn('STUDENT_ID_BACK', codigos)
        self.assertTrue(all(r.cumplido for r in calcular_requisitos_documentales(solicitud)))

    def test_soporte_aportado_pendiente_o_rechazado_no_se_ignora(self):
        solicitud, estudiante, _ = self.expediente()
        documento = self._documento(solicitud, estudiante, Tipo.INCOME_CERTIFICATE, 'ingreso-aportado')
        requisito = next(r for r in construir_politica_documental(solicitud) if r.tipo == Tipo.INCOME_CERTIFICATE)
        self.assertFalse(requisito_listo_para_aprobacion(requisito))
        with self.assertRaises(ValidationError):
            validar_expediente_para_aprobacion(solicitud)
        documento.estado_validacion = 'REJECTED'
        documento.save(update_fields=['estado_validacion'])
        with self.assertRaises(ValidationError):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=fixtures.SCAN_INFECTED)
    def test_soporte_opcional_aportado_malware_impide_firma(self):
        solicitud, estudiante, _ = self.expediente()
        documento = self._documento(solicitud, estudiante, Tipo.INCOME_CERTIFICATE, 'ingreso-maligno')
        from financiacion_educativa.services.escaneo_documentos import procesar_escaneo_documento
        from financiacion_educativa.tests.scan_backends import BackendInfectado

        procesar_escaneo_documento(documento=documento, backend=BackendInfectado(), origen='AUTOMATIC')
        with self.assertRaises(ValidationError):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        solicitud.estado = Estado.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])
        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        self.assertNotEqual(resultado.codigo, 'PENDING_SIGNATURE')
        self.assertFalse(ProcesoFirmaEducativa.objects.exists())

    def test_no_hay_fuga_ni_se_infiere_regla_del_curso(self):
        solicitud, _, _ = self.expediente()
        otra = crear_institucion('OTRA')
        self.assertTrue(otra.requiere_soporte_ingresos)
        self.assertFalse(soporte_ingresos_obligatorio(solicitud))
        solicitud.institucion = otra
        self.assertTrue(soporte_ingresos_obligatorio(solicitud))
        self.assertEqual(solicitud.nombre_curso, 'Tecnico en Sistemas')

    def test_menor_sin_ingresos_conserva_tutor_y_firmante(self):
        solicitud, estudiante, tutor = self.expediente(menor=True)
        self.assertEqual(solicitud.roles_participantes.get(rol=RolParticipante.PRINCIPAL_DEBTOR).participante_id, tutor.pk)
        solicitud = completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        self.assertEqual(resultado.codigo, 'PENDING_SIGNATURE')
        self.assertEqual(fixtures.RecordingEducationalSignatureBackend.submissions[0]['firmante'].pk, tutor.pk)
        self.assertNotEqual(tutor.pk, estudiante.pk)

    def test_formulario_y_documentacion_explican_opcional_sin_quitar_categorias(self):
        solicitud, estudiante, _ = self.expediente()
        form = DocumentoFinanciacionForm(solicitud=solicitud, tipo_inicial=Tipo.INCOME_CERTIFICATE)
        self.assertIn((Tipo.INCOME_CERTIFICATE, 'Soporte de ingresos - opcional'), form.fields['tipo'].choices)
        self.assertTrue(form.fields['archivo'].required)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('financiacion_educativa_web:documentacion', args=[solicitud.pk]))
        self.assertContains(respuesta, 'Soporte de ingresos &mdash; opcional')
        self.assertContains(respuesta, 'Puedes continuar sin cargarlo')
        carga = self.client.get(reverse('financiacion_educativa_web:documento-cargar', args=[solicitud.pk]), {'tipo': Tipo.INCOME_CERTIFICATE, 'participante': estudiante.pk})
        self.assertContains(carga, 'extracto bancario')
        self.assertContains(carga, 'certificaci&oacute;n bancaria de titularidad')

    def test_correccion_historica_no_exige_ingreso_opcional_ausente(self):
        solicitud, _, _ = self.expediente(opcional=False)
        solicitud.estado = Estado.CORRECTION_REQUIRED
        solicitud.save(update_fields=['estado'])
        decision = DecisionRevisionEducativa.objects.create(
            solicitud=solicitud, tipo=TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
            motivo=MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
            mensaje_solicitante='Carga el soporte de ingresos.',
            requisitos_pendientes=['INCOME_CERTIFICATE'], responsable=self.usuario,
        )
        solicitud.institucion.requiere_soporte_ingresos = False
        solicitud.institucion.save(update_fields=['requiere_soporte_ingresos'])
        self.assertTrue(all(r.cumplido for r in calcular_requisitos_documentales(solicitud)))
        self.assertEqual(obtener_progreso_publico(solicitud)['correction_requirements'], [])
        self.client.force_login(self.usuario)
        pagina = self.client.get(reverse('financiacion_educativa_web:documentacion', args=[solicitud.pk]))
        self.assertNotContains(pagina, 'Carga el soporte de ingresos.')
        decision.refresh_from_db()
        self.assertEqual(decision.mensaje_solicitante, 'Carga el soporte de ingresos.')
        completar_fase_documental(solicitud=solicitud, actor=self.usuario)

    def test_configuracion_no_reinterpreta_contratacion_ni_firma(self):
        solicitud, _, _ = self.expediente()
        solicitud = completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        solicitud.refresh_from_db()
        fotografia = CondicionesFinancieras.objects.get(solicitud=solicitud)
        eventos = list(solicitud.historial_estados.values('id', 'metadata'))
        solicitud.institucion.requiere_soporte_ingresos = True
        solicitud.institucion.save(update_fields=['requiere_soporte_ingresos'])
        self.assertFalse(soporte_ingresos_obligatorio(solicitud))
        self.assertEqual(list(solicitud.historial_estados.values('id', 'metadata')), eventos)
        self.assertEqual(CondicionesFinancieras.objects.get(pk=fotografia.pk).capital_financiado, fotografia.capital_financiado)
        self.assertEqual(self._firmar(ProcesoFirmaEducativa.objects.get(solicitud=solicitud)).status_code, 200)
        solicitud.refresh_from_db()
        anterior = obtener_resultado_publico(solicitud)
        self.assertFalse(soporte_ingresos_obligatorio(solicitud))
        self.assertEqual(obtener_resultado_publico(solicitud), anterior)

    def test_admin_expone_el_ajuste_y_no_modifica_instituciones_al_consultar(self):
        institucion = crear_institucion('ADMIN')
        request = RequestFactory().get('/')
        request.user = self.usuario
        self.assertIn('requiere_soporte_ingresos', admin.site._registry[Institucion].get_fields(request, institucion))
        institucion.refresh_from_db()
        self.assertTrue(institucion.requiere_soporte_ingresos)

    def test_revision_manual_aprueba_sin_exigir_documento_opcional(self):
        from financiacion_educativa.services.orquestacion_automatica import ejecutar_etapa_persistente

        solicitud, _, _ = self.expediente()
        solicitud = completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        ejecutar_etapa_persistente(solicitud_id=solicitud.pk, etapa='SECURITY_SCAN')
        ejecutar_etapa_persistente(solicitud_id=solicitud.pk, etapa='DOCUMENT_VALIDATION')
        self.usuario.is_superuser = True
        self.usuario.save(update_fields=['is_superuser'])
        decision = decidir_solicitud(solicitud=solicitud, actor=self.usuario,
            tipo=TipoDecisionRevisionEducativa.APPROVED,
            motivo=MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, Estado.PENDING_PROMISSORY_NOTE)
        traza = solicitud.historial_estados.get(estado_nuevo=Estado.PENDING_PROMISSORY_NOTE)
        self.assertFalse(traza.metadata['politica_documental']['soporte_ingresos_obligatorio'])
        self.assertIsNotNone(decision.fotografia_financiera_id)

    def test_revision_no_solicita_correccion_de_soporte_opcional_ausente(self):
        solicitud, _, _ = self.expediente()
        solicitud = completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        self.usuario.is_superuser = True
        self.usuario.save(update_fields=['is_superuser'])
        with self.assertRaisesMessage(ValidationError, 'documento opcional'):
            decidir_solicitud(solicitud=solicitud, actor=self.usuario,
                tipo=TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
                motivo=MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
                mensaje_solicitante='Aporta ingresos.', requisitos_pendientes=['INCOME_CERTIFICATE'])
        self.assertFalse(solicitud.decisiones_revision.exists())

    def test_legado_autorizado_sin_traza_conserva_politica_obligatoria(self):
        solicitud, _, _ = self.expediente()
        solicitud.estado = Estado.APPROVED
        solicitud.save(update_fields=['estado'])
        anteriores = list(solicitud.historial_estados.values())
        self.assertTrue(soporte_ingresos_obligatorio(solicitud))
        self.assertEqual(list(solicitud.historial_estados.values()), anteriores)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, Estado.APPROVED)

    def test_traza_conserva_la_regla_evaluada_aunque_cambie_configuracion(self):
        solicitud, _, _ = self.expediente()
        politica_evaluada = construir_politica_documental(solicitud)
        solicitud.institucion.requiere_soporte_ingresos = True
        solicitud.institucion.save(update_fields=['requiere_soporte_ingresos'])
        self.assertTrue(soporte_ingresos_obligatorio(solicitud))
        self.assertFalse(resumir_politica_documental(solicitud, requisitos=politica_evaluada)['soporte_ingresos_obligatorio'])

    def test_cambiar_configuracion_no_encola_ni_revalida_por_si_solo(self):
        solicitud, _, _ = self.expediente(opcional=False)
        documentos = list(solicitud.documentos.values('id', 'estado_escaneo', 'estado_validacion', 'resultado_procesamiento'))
        historial = list(solicitud.historial_estados.values())
        solicitud.institucion.requiere_soporte_ingresos = False
        solicitud.institucion.save(update_fields=['requiere_soporte_ingresos'])
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, Estado.PENDING_DOCUMENT)
        self.assertFalse(solicitud.procesos_automatizacion.exists())
        self.assertEqual(list(solicitud.documentos.values('id', 'estado_escaneo', 'estado_validacion', 'resultado_procesamiento')), documentos)
        self.assertEqual(list(solicitud.historial_estados.values()), historial)
