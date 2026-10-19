from decimal import Decimal
from uuid import uuid4
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models_recaudos import ArchivoRecaudoEducativo, IntentoEscaneoRecaudo, ConciliacionRecaudoEducativo, ReporteRecaudoEducativo
from financiacion_educativa.services import recaudos as servicios
from financiacion_educativa.web.forms_recaudos import ConfirmarRecaudoForm
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO, DatosRecaudosMixin
from financiacion_educativa.tests.factories import imagen_jpeg_prueba


@override_settings(**AISLAMIENTO)
class RecaudosUXTests(DatosRecaudosMixin, TestCase):
    def setUp(self):
        self.preparar_recaudos()
        self.reporte, _ = self.reportar()
        self.escanear(self.reporte.comprobante_id)
        self.client.force_login(self.actor)

    def accion_url(self, accion):
        return reverse(f'financiacion_educativa_web:operaciones:recaudo-{accion}', args=[self.reporte.pk])

    def datos(self, **cambios):
        datos = dict(monto='100000.00', fecha=timezone.localdate().isoformat(), referencia='BANCO-UX-001',
            evidencia_id=str(self.evidencia().pk), observacion='Verificacion sintetica.')
        datos.update(cambios)
        return datos

    def test_confirmacion_invalida_preserva_valores_y_error_accesible(self):
        datos = self.datos(monto='-2', referencia='REF-CONSERVADA')
        respuesta = self.client.post(self.accion_url('confirmar'), datos)
        self.assertEqual(respuesta.status_code, 200)
        form = respuesta.context['confirmar_form']
        self.assertTrue(form.is_bound)
        self.assertIn('monto', form.errors)
        self.assertEqual(form['referencia'].value(), 'REF-CONSERVADA')
        self.assertContains(respuesta, 'role="alert"')
        self.assertContains(respuesta, 'href="#id_monto"')
        self.assertFalse(respuesta.context['evidencia_form'].is_bound)
        self.assertFalse(respuesta.context['rechazar_form'].is_bound)
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_error_de_servicio_preserva_formulario_sin_redireccion(self):
        datos = self.datos(monto='50000.00')
        respuesta = self.client.post(self.accion_url('confirmar'), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['confirmar_form']['monto'].value(), '50000.00')
        self.assertIn('justificacion_diferencia', respuesta.context['confirmar_form'].errors)
        self.assertContains(respuesta, 'Explique expresamente la diferencia')
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_evidencia_invalida_exige_reseleccionar_sin_fingir_carga(self):
        respuesta = self.client.post(self.accion_url('evidencia'), {
            'archivo': SimpleUploadedFile('invalido.pdf', b'not a PDF'),
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('archivo', respuesta.context['evidencia_form'].errors)
        self.assertContains(respuesta, 'el navegador no conserva el archivo adjunto')
        self.assertFalse(respuesta.context['confirmar_form'].is_bound)

    def test_rechazo_invalido_no_pierde_formulario_ni_modifica_estado(self):
        respuesta = self.client.post(self.accion_url('rechazar'), {'motivo': ' '})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('motivo', respuesta.context['rechazar_form'].errors)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertFalse(hasattr(self.reporte, 'rechazo'))

    def test_exito_redirige_y_replay_preserva_ingreso(self):
        datos = self.datos()
        primero = self.client.post(self.accion_url('confirmar'), datos)
        repetido = self.client.post(self.accion_url('confirmar'), datos)
        self.assertEqual(primero.status_code, 302)
        self.assertEqual(repetido['Location'], primero['Location'])
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
        self.assertContains(self.client.get(primero['Location']), 'Ingreso confirmado pendiente de aplicaci')

    def test_filtro_institucional_por_nombre_y_uuid_validado(self):
        url = reverse('financiacion_educativa_web:operaciones:recaudos')
        respuesta = self.client.get(url, {'institucion': str(self.institucion.pk)})
        self.assertContains(respuesta, f'value="{self.institucion.pk}" selected')
        self.assertContains(respuesta, self.institucion.nombre_comercial)
        self.assertNotContains(respuesta, 'Institucion (UUID)')
        self.assertEqual(respuesta.context['pagina'].paginator.count, 1)
        invalida = self.client.get(url, {'institucion': str(uuid4())})
        self.assertIn('institucion', invalida.context['form'].errors)
        self.assertEqual(invalida.context['pagina'].paginator.count, 0)

    @override_settings(LANGUAGE_CODE='es-co')
    def test_post_cop_punto_decimal_conserva_precision_y_coma_ambigua_falla(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        datos = dict(clave=str(uuid4()), receptor_id=str(self.receptor.pk), proposito='INSTALLMENT',
            monto='157738.25', fecha=timezone.localdate().isoformat(), referencia='UX-SINTETICO')
        valida = self.client.post(url, {**datos, 'archivo': imagen_jpeg_prueba()})
        self.assertEqual(valida.status_code, 302)
        self.assertEqual(ReporteRecaudoEducativo.objects.get(clave_idempotencia=datos['clave']).monto_reportado, Decimal('157738.25'))
        for monto in ('157.738,25', '157738,25', '1.001', 'NaN', '0', '100000000000000'):
            with self.subTest(monto=monto):
                respuesta = self.client.post(url, {**datos, 'clave': str(uuid4()), 'monto': monto, 'archivo': imagen_jpeg_prueba()})
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn('monto', respuesta.context['form'].errors)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 2)

    def test_estado_humano_y_qr_sigue_vinculado_a_version(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]))
        qr = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        self.assertContains(respuesta, f'href="{qr}?download=1"')
        estado = self.client.get(reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, self.reporte.pk]))
        self.assertContains(estado, 'Sin hallazgos de seguridad')
        self.assertContains(estado, 'Consultar nuevamente')
        self.assertNotContains(estado, '>SAFE<')

    def test_selector_solo_ofrece_evidencia_propia_bancaria_con_hash_limpio(self):
        propia = self.evidencia()
        pendiente = servicios.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=self.actor)
        ajeno = get_user_model().objects.create_superuser('otro-operador', 'otro@example.test', 'test-only')
        otra = servicios.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=ajeno)
        self.escanear(otra.pk)
        hash_incoherente = self.evidencia()
        # Estado de escaneo sintetico incongruente: no se modifica evidencia historica.
        intento = IntentoEscaneoRecaudo.objects.create(archivo=hash_incoherente, numero=2, sha256='0' * 64)
        intento.estado = 'CLEAN'
        intento._transicion_oficial = True
        intento.save()
        respuesta = self.client.get(self.accion_url('detalle'))
        form = respuesta.context['confirmar_form']
        self.assertEqual(list(form.fields['evidencia_id'].queryset.values_list('pk', flat=True)), [propia.pk])
        self.assertContains(respuesta, '<select name="evidencia_id"')
        self.assertContains(respuesta, 'Sin hallazgos de seguridad')
        self.assertNotContains(respuesta, 'UUID de evidencia bancaria')
        for archivo in (pendiente, otra, hash_incoherente, self.receptor.qr, self.reporte.comprobante):
            with self.subTest(finalidad=archivo.finalidad):
                datos = self.datos(evidencia_id=str(archivo.pk))
                invalida = self.client.post(self.accion_url('confirmar'), datos)
                self.assertEqual(invalida.status_code, 200)
                self.assertIn('evidencia_id', invalida.context['confirmar_form'].errors)
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_selector_sin_actor_falla_cerrado(self):
        self.evidencia()
        self.assertFalse(ConfirmarRecaudoForm().fields['evidencia_id'].queryset.exists())

    def test_version_renderizada_no_autoriza_evidencia_que_dejo_de_ser_safe(self):
        propia = self.evidencia()
        self.client.get(self.accion_url('detalle'))
        IntentoEscaneoRecaudo.objects.create(archivo=propia, numero=2, sha256=propia.sha256)
        datos = self.datos(evidencia_id=str(propia.pk))
        respuesta = self.client.post(self.accion_url('confirmar'), datos)
        self.assertIn('evidencia_id', respuesta.context['confirmar_form'].errors)
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte, propia))
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_servicio_revalida_estado_actual_aun_con_selector_previo(self):
        propia = self.evidencia()
        datos = self.datos_confirmacion(self.reporte, propia)
        with patch.object(ArchivoRecaudoEducativo, 'estado_escaneo', new_callable=PropertyMock, return_value='PENDING'):
            with self.assertRaises(ValidationError):
                servicios.confirmar_recaudo(**datos)
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_evidencia_ajena_o_no_bancaria_no_entra_por_servicio_directo(self):
        ajeno = get_user_model().objects.create_superuser('ajeno-servicio', 'ajeno-servicio@example.test', 'test-only')
        otra = servicios.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=ajeno)
        self.escanear(otra.pk)
        for evidencia in (otra, self.receptor.qr, self.reporte.comprobante):
            with self.subTest(finalidad=evidencia.finalidad), self.assertRaises(PermissionDenied):
                servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte, evidencia))

    def test_evidencia_inexistente_o_malformada_es_error_de_campo(self):
        for pk in (uuid4(), 'no-es-un-uuid'):
            with self.subTest(pk=pk), self.assertRaises(ValidationError) as error:
                servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte, evidencia_id=pk))
            self.assertIn('evidencia_id', error.exception.message_dict)

    def test_contenido_alterado_o_ausente_impide_confirmar_sin_filtrar_rutas(self):
        propia = self.evidencia()
        datos = self.datos(evidencia_id=str(propia.pk))
        with propia.archivo.storage.open(propia.archivo.name, 'wb') as archivo:
            archivo.write(b'contenido-sintetico-alterado')
        respuesta = self.client.post(self.accion_url('confirmar'), datos)
        self.assertIn('evidencia_id', respuesta.context['confirmar_form'].errors)
        self.assertContains(respuesta, 'integridad del soporte')
        self.assertNotContains(respuesta, propia.archivo.name)
        propia.archivo.storage.delete(propia.archivo.name)
        ausente = self.client.post(self.accion_url('confirmar'), datos)
        self.assertIn('evidencia_id', ausente.context['confirmar_form'].errors)
        self.assertNotContains(ausente, self.temporal.name)
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_comprobante_alterado_tambien_bloquea_confirmacion(self):
        with self.reporte.comprobante.archivo.storage.open(self.reporte.comprobante.archivo.name, 'wb') as archivo:
            archivo.write(b'comprobante-alterado')
        with self.assertRaisesMessage(ValidationError, 'integridad del soporte'):
            servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte))
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_observacion_vacia_normaliza_y_replay_no_duplica(self):
        datos = self.datos(observacion='')
        primero = self.client.post(self.accion_url('confirmar'), datos)
        repetido = self.client.post(self.accion_url('confirmar'), {**datos, 'observacion': '  '})
        self.assertEqual(primero.status_code, 302)
        self.assertEqual(repetido['Location'], primero['Location'])
        ingreso = ConciliacionRecaudoEducativo.objects.get()
        self.assertEqual(ingreso.observacion, servicios.OBSERVACION_SIN_ADICIONAL)
        self.assertEqual(ingreso.actor_id, self.actor.pk)
        self.assertIsNotNone(ingreso.confirmada_en)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_observacion_historica_no_se_reescribe(self):
        datos = self.datos()
        self.client.post(self.accion_url('confirmar'), datos)
        ingreso = ConciliacionRecaudoEducativo.objects.get()
        fecha = ingreso.confirmada_en
        respuesta = self.client.post(self.accion_url('confirmar'), {**datos, 'observacion': ''})
        self.assertContains(respuesta, 'La conciliacion ya fue registrada con otros datos.')
        ingreso.refresh_from_db()
        self.assertEqual(ingreso.observacion, datos['observacion'])
        self.assertEqual(ingreso.confirmada_en, fecha)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_diferencia_justificada_y_evidencia_reutilizable_no_exigen_otra_carga(self):
        propia = self.evidencia()
        datos = self.datos(monto='90000.00', evidencia_id=str(propia.pk), observacion='',
            justificacion_diferencia='El banco registra noventa mil pesos.')
        self.assertEqual(self.client.post(self.accion_url('confirmar'), datos).status_code, 302)
        otro, _ = self.reportar()
        self.escanear(otro.comprobante_id)
        servicios.confirmar_recaudo(**self.datos_confirmacion(otro, propia, referencia='BANCO-UX-OTRO', observacion=''))
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 2)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.filter(evidencia_movimiento=propia).count(), 2)

    def test_referencia_invalida_y_movimiento_duplicado_son_errores_visibles(self):
        propia = self.evidencia()
        datos = self.datos(evidencia_id=str(propia.pk), referencia='REF CON ESPACIO')
        respuesta = self.client.post(self.accion_url('confirmar'), datos)
        self.assertIn('referencia', respuesta.context['confirmar_form'].errors)
        self.assertContains(respuesta, 'href="#id_referencia"')
        servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte, propia, referencia='UNICO-UX'))
        otro, _ = self.reportar()
        self.escanear(otro.comprobante_id)
        url = reverse('financiacion_educativa_web:operaciones:recaudo-confirmar', args=[otro.pk])
        duplicado = self.client.post(url, {**datos, 'referencia': 'unico-ux'})
        self.assertIn('referencia', duplicado.context['confirmar_form'].errors)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_escaneo_pendiente_tiene_consulta_sin_recargar_archivo(self):
        self.client.post(self.accion_url('evidencia'), {'archivo': imagen_jpeg_prueba()})
        pendiente = ArchivoRecaudoEducativo.objects.get(finalidad='BANK_EVIDENCE')
        total = ArchivoRecaudoEducativo.objects.count()
        respuesta = self.client.get(self.accion_url('detalle'))
        self.assertContains(respuesta, 'Verificaci')
        self.assertFalse(respuesta.context['confirmar_form'].fields['evidencia_id'].queryset.exists())
        self.escanear(pendiente.pk)
        actualizada = self.client.get(self.accion_url('detalle'))
        self.assertTrue(actualizada.context['confirmar_form'].fields['evidencia_id'].queryset.filter(pk=pendiente.pk).exists())
        self.assertEqual(ArchivoRecaudoEducativo.objects.count(), total)
