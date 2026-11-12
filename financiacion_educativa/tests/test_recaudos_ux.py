from decimal import Decimal
from uuid import uuid4
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
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

    def test_pagos_compacto_sin_identificadores_y_detalles_nativos(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]))
        self.assertContains(respuesta, 'Cuota pactada en el contrato')
        self.assertContains(respuesta, 'no un saldo real')
        self.assertContains(respuesta, 'id="reportar-transferencia">')
        self.assertContains(respuesta, 'href="#pagar-breb"')
        self.assertContains(respuesta, 'Ya realic&eacute; la transferencia')
        self.assertNotContains(respuesta, 'galer&iacute;a')
        self.assertContains(respuesta, 'id="formulario-reporte" tabindex="-1"')
        self.assertEqual(respuesta.context['form']['proposito'].value(), 'INSTALLMENT')
        from django.utils.html import strip_tags
        texto = strip_tags(respuesta.content.decode())
        self.assertNotIn(str(self.solicitud.pk), texto)
        self.assertNotIn(self.solicitud.referencia_externa, texto)
        self.assertNotIn(str(self.reporte.pk), texto)

    def test_error_abre_datos_adicionales_y_conserva_proposito_referencia(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.post(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]), {
            'clave': str(uuid4()), 'receptor_id': str(self.receptor.pk), 'monto': '-1',
            'fecha': timezone.localdate().isoformat(), 'proposito': 'PRINCIPAL', 'referencia': 'CONSERVADA',
        })
        self.assertContains(respuesta, 'id="reportar-transferencia" open')
        self.assertEqual(respuesta.context['form']['referencia'].value(), 'CONSERVADA')
        self.assertEqual(respuesta.context['form']['proposito'].value(), 'PRINCIPAL')

    def test_seguimiento_no_presenta_aplicacion_como_completada(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, self.reporte.pk]))
        self.assertContains(respuesta, 'aria-labelledby="estado-reporte"')
        self.assertContains(respuesta, 'El reporte no acredita cuota pagada ni saldo actualizado')
        self.assertContains(respuesta, 'No necesitas volver a transferir')
        self.assertNotContains(respuesta, 'edu-recaudos-progress')

    def test_conciliacion_justificacion_desplegable_sin_js_y_observacion_secundaria(self):
        respuesta = self.client.get(self.accion_url('detalle'))
        self.assertContains(respuesta, 'id="observacion-adicional"><summary>')
        self.assertContains(respuesta, 'name="justificacion_diferencia"')
        self.assertContains(respuesta, 'data-monto-reportado="100000.00"><summary>')
        self.assertContains(respuesta, 'Confirma un ingreso bancario, no una cuota pagada')

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
        self.assertContains(respuesta, 'data-monto-reportado="100000.00" open')
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_diferencia_con_error_conserva_justificacion_abierta_y_datos(self):
        respuesta = self.client.post(self.accion_url('confirmar'), self.datos(
            monto='50000.00', referencia='REFERENCIA INVALIDA',
            justificacion_diferencia='Ingreso menor al declarado, verificado en extracto.',
        ))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data-monto-reportado="100000.00" open')
        form = respuesta.context['confirmar_form']
        self.assertEqual(form['monto'].value(), '50000.00')
        self.assertEqual(form['justificacion_diferencia'].value(), 'Ingreso menor al declarado, verificado en extracto.')
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_transferencia_confirmada_muestra_condicion_pendiente_una_vez(self):
        servicios.confirmar_recaudo(**self.datos_confirmacion(self.reporte))
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, self.reporte.pk]))
        self.assertContains(respuesta, '<h1 id="estado-reporte">Transferencia confirmada</h1>', html=True)
        self.assertContains(respuesta, 'La aplicaci&oacute;n a tu cuota est&aacute; pendiente', count=1)
        self.assertContains(respuesta, 'Verificaci&oacute;n de seguridad completada')
        self.assertNotContains(respuesta, 'Archivo verificado')
        self.assertNotContains(respuesta, 'paz y salvo')

    def test_estado_de_seguridad_no_ofrece_descarga_insegura(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, self.reporte.pk])
        for estado, mensaje in [('PENDING', 'pendiente de verificaci'),
                                ('ERROR', 'reintentar la verificaci'),
                                ('BLOCKED', 'bloqueado por seguridad')]:
            with self.subTest(estado=estado), patch.object(ArchivoRecaudoEducativo, 'estado_escaneo',
                    new_callable=PropertyMock, return_value=estado):
                respuesta = self.client.get(url)
                self.assertContains(respuesta, mensaje)
                self.assertNotContains(respuesta, 'Descargar comprobante')
                self.assertNotContains(respuesta, 'Transferencia confirmada')

    def test_reporte_rechazado_muestra_motivo_y_retorno_seguro_sin_rechazar_contrato(self):
        servicios.rechazar_reporte(reporte_id=self.reporte.pk, actor=self.actor,
            motivo='No se encontro el movimiento. Verifica la referencia con tu banco.')
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, self.reporte.pk]))
        self.assertContains(respuesta, 'No se encontro el movimiento')
        self.assertContains(respuesta, 'Corregir con un nuevo reporte')
        self.assertContains(respuesta, '#formulario-reporte')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')

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
        self.assertRedirects(duplicado, reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[self.reporte.pk]))
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

    def test_finanzas_enlaza_pagos_solo_para_obligacion_real_y_flag_activo(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk])
        pagos = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        self.assertContains(self.client.get(url), f'href="{pagos}"')
        with override_settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False):
            self.assertNotContains(self.client.get(url), f'href="{pagos}"')
            self.assertEqual(self.client.get(pagos).status_code, 404)
        from financiacion_educativa.models import ProcesoFirmaEducativa

        ProcesoFirmaEducativa.objects.filter(solicitud=self.solicitud).update(estado='SENT')
        self.assertNotContains(self.client.get(url), f'href="{pagos}"')
        self.assertEqual(self.client.get(pagos).status_code, 404)

    def test_finanzas_y_pagos_no_acceden_por_ser_operador_sin_propiedad(self):
        for nombre in ('finanzas', 'pagos'):
            self.assertEqual(self.client.get(reverse(f'financiacion_educativa_web:{nombre}', args=[self.solicitud.pk])).status_code, 404)

    def test_qr_alterado_no_se_consulta_ni_permite_nuevos_reportes(self):
        qr = self.receptor.qr
        with qr.archivo.storage.open(qr.archivo.name, 'wb') as stream:
            stream.write(b'qr-sintetico-alterado')
        self.assertEqual(qr.estado_escaneo, 'SAFE')
        self.assertIsNone(servicios.receptor_vigente())
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 409)
        self.assertNotContains(respuesta, qr.archivo.name, status_code=409)
        with self.assertRaises(ValidationError):
            self.reportar()
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)

    def test_activacion_revalida_qr_limpio_y_no_anexa_evento_si_falta_archivo(self):
        qr = self.receptor.qr
        anteriores = self.receptor.eventos.count()
        qr.archivo.storage.delete(qr.archivo.name)
        with self.assertRaisesMessage(ValidationError, 'soporte no esta disponible'):
            servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Verificacion sintetica')
        self.assertEqual(self.receptor.eventos.count(), anteriores)
        self.assertIsNone(servicios.receptor_vigente())

    def test_descarga_comprobante_revalida_bytes_y_no_filtra_ruta(self):
        archivo = self.reporte.comprobante
        with archivo.archivo.storage.open(archivo.archivo.name, 'wb') as stream:
            stream.write(b'comprobante-sintetico-alterado')
        self.client.force_login(self.pagador)
        respuesta = self.client.get(reverse('financiacion_educativa_web:pago-comprobante', args=[self.solicitud.pk, self.reporte.pk]))
        self.assertEqual(respuesta.status_code, 404)
        self.assertNotContains(respuesta, archivo.archivo.name, status_code=404)
        self.assertNotContains(respuesta, self.temporal.name, status_code=404)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(self.accion_url('soporte')).status_code, 404)

    def test_post_receptor_reemplazado_exige_recarga_sin_ofrecer_qr_nuevo(self):
        from financiacion_educativa.tests.test_recaudos_breb import pdf_sintetico

        nuevo = servicios.crear_receptor(entidad='OTRO-BANCO', identificador_cuenta='OTRA-CUENTA',
            titular='RECEPTOR NUEVO SINTETICO', llave='LLAVE-NUEVA-SINTETICA', qr=pdf_sintetico(), actor=self.actor)
        self.escanear(nuevo.qr_id)
        servicios.activar_receptor(receptor_id=nuevo.pk, actor=self.actor, motivo='Cambio sintetico')
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        datos = dict(clave=str(uuid4()), receptor_id=str(self.receptor.pk), proposito='INSTALLMENT',
            monto='100000.00', fecha=timezone.localdate().isoformat(), referencia='CAMBIO-RECEPTOR')
        respuesta = self.client.post(url, {**datos, 'archivo': imagen_jpeg_prueba()})
        self.assertContains(respuesta, 'Recargar datos de pago')
        self.assertContains(respuesta, 'CAMBIO-RECEPTOR')
        self.assertContains(respuesta, '100000.00')
        self.assertNotContains(respuesta, 'LLAVE-NUEVA-SINTETICA')
        self.assertNotContains(respuesta, reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, nuevo.pk]))
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        self.assertContains(self.client.get(url), 'LLAVE-NUEVA-SINTETICA')

    def test_replay_previo_al_cambio_receptor_sigue_reutilizando_reporte(self):
        clave = uuid4()
        original, _ = self.reportar(clave=clave, referencia='REPLAY-ROTACION')
        servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Pausa sintetica', habilitado=False)
        self.client.force_login(self.pagador)
        datos = dict(clave=str(clave), receptor_id=str(self.receptor.pk), proposito='INSTALLMENT',
            monto='100000.00', fecha=timezone.localdate().isoformat(), referencia='REPLAY-ROTACION')
        respuesta = self.client.post(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]), {**datos, 'archivo': imagen_jpeg_prueba()})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta['Location'], reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, original.pk]))
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 2)

    def test_post_vacio_muestra_errores_de_formulario_no_rotacion(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.post(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]), {})
        self.assertTrue(respuesta.context['form'].is_bound)
        self.assertContains(respuesta, 'Revisa los datos antes de continuar')
        self.assertFalse(respuesta.context['receptor_cambio'])

    def test_respuesta_sirve_los_bytes_verificados_no_un_archivo_reabierto(self):
        from financiacion_educativa.web.recaudos import comprobante_view

        archivo = self.reporte.comprobante
        with archivo.archivo.open('rb') as stream:
            originales = stream.read()
        request = RequestFactory().get('/')
        request.user = self.pagador
        respuesta = comprobante_view(request, self.solicitud.pk, self.reporte.pk)
        try:
            with archivo.archivo.storage.open(archivo.archivo.name, 'wb') as stream:
                stream.write(b'contenido-alterado-despues-de-verificar')
            self.assertEqual(b''.join(respuesta.streaming_content), originales)
        finally:
            respuesta.file_to_stream.close()
