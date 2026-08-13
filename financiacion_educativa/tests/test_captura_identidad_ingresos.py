from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCapturaMovil,
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import EnlaceCapturaMovil
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.web.views import SESSION_CAPTURA_MOVIL_GRANT


MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) '
    'AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36'
)


def jpeg(nombre='captura.jpg', marca=b'captura'):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='image/jpeg',
    )


def pdf(nombre='ingresos.pdf', marca=b'ingresos'):
    return SimpleUploadedFile(
        nombre,
        b'%PDF-1.7\n' + marca + b'\n%%EOF',
        content_type='application/pdf',
    )


class CapturaIdentidadEIngresosTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)

        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='captura@example.com',
            email='captura@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='captura-otro@example.com',
            email='captura-otro@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.estudiante = self._crear_participante(
            nacimiento=date(1990, 1, 1),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.url_camara = reverse(
            'financiacion_educativa_web:capturar-identidad',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'persona': 'estudiante',
            },
        )

    def _crear_participante(
        self,
        *,
        nacimiento,
        roles,
        numero='1000200030',
        relacion=RelacionEstudiante.SELF,
        tipo_documento=TipoDocumentoIdentidad.CC,
        participante_id=None,
    ):
        return registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Persona',
                apellidos='Prueba',
                tipo_documento=tipo_documento,
                numero_documento=numero,
                fecha_nacimiento=nacimiento,
                relacion_estudiante=relacion,
            ),
            roles=roles,
            participante_id=participante_id,
        )

    def _capturar(self, lado, archivo):
        return self.client.post(
            self.url_camara,
            {'lado': lado, 'captura': archivo},
        )

    def _autorizar_captura_movil(self, cliente=None):
        cliente = cliente or self.client
        cliente.defaults['HTTP_USER_AGENT'] = MOBILE_UA
        cliente.force_login(self.usuario)
        enlace = EnlaceCapturaMovil.objects.create(
            solicitud=self.solicitud,
            persona=RolParticipante.STUDENT,
            token_hash='1' * 64,
            destinatario_hmac='2' * 64,
            estado=EstadoEnlaceCapturaMovil.CONSUMED,
            estado_entrega=EstadoEntregaCapturaMovil.SENT,
            vence_en=timezone.now() + timedelta(minutes=10),
            creada_por=self.usuario,
            consumida_por=self.usuario,
            consumida_en=timezone.now(),
        )
        sesion = cliente.session
        sesion[SESSION_CAPTURA_MOVIL_GRANT] = {
            'enlace_id': str(enlace.pk),
            'solicitud_id': str(self.solicitud.pk),
            'persona': 'estudiante',
        }
        sesion.save()
        return enlace

    def test_pagina_movil_guia_camara_y_ofrece_fallback_controlado(self):
        self._autorizar_captura_movil()

        respuesta = self.client.get(self.url_camara)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data-camera-capture')
        self.assertContains(respuesta, 'data-camera-video')
        self.assertContains(respuesta, 'type="file"')
        self.assertContains(respuesta, 'accept="image/*"')
        self.assertContains(respuesta, 'capture="environment"')
        self.assertContains(respuesta, 'data-min-width="800"')
        self.assertContains(respuesta, 'data-min-height="500"')
        javascript = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'js'
            / 'financiacion_educativa.js'
        ).read_text(encoding='utf-8')
        self.assertIn('navigator.mediaDevices.getUserMedia', javascript)
        self.assertIn("facingMode: { ideal: 'environment' }", javascript)
        self.assertIn('NotAllowedError', javascript)
        self.assertIn('NotFoundError', javascript)
        self.assertIn('track.stop()', javascript)
        self.assertIn('const evaluarCalidad', javascript)
        self.assertIn('const variance', javascript)
        repeat_handler = javascript.split(
            "repeatButton.addEventListener('click'", 1
        )[1].split('});', 1)[0]
        self.assertIn('stopCamera();', repeat_handler)
        self.assertIn("window.addEventListener('pagehide', stopCamera)", javascript)

    def test_captura_frente_y_reverso_como_evidencias_distintas(self):
        self._autorizar_captura_movil()

        frente = self._capturar('frente', jpeg(marca=b'frente'))
        reverso = self._capturar('reverso', jpeg(marca=b'reverso'))

        self.assertEqual(frente.status_code, 200)
        self.assertEqual(reverso.status_code, 200)
        documentos = self.solicitud.documentos.filter(
            participante=self.estudiante,
            activo=True,
        )
        self.assertEqual(documentos.count(), 2)
        self.assertSetEqual(
            set(documentos.values_list('tipo', flat=True)),
            {
                TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            },
        )
        self.assertTrue(
            all(
                origen == OrigenCapturaDocumento.CAMERA
                for origen in documentos.values_list(
                    'origen_captura',
                    flat=True,
                )
            )
        )

    def test_pasaporte_solo_admite_pagina_biografica_como_frente(self):
        self.estudiante = self._crear_participante(
            nacimiento=date(1990, 1, 1),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
            tipo_documento=TipoDocumentoIdentidad.PASSPORT,
            participante_id=self.estudiante.pk,
        )
        self._autorizar_captura_movil()

        pagina = self.client.get(self.url_camara)
        reverso = self._capturar('reverso', jpeg(marca=b'reverso'))
        frente = self._capturar('frente', jpeg(marca=b'biografica'))
        requisitos = {
            requisito.codigo
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }

        self.assertNotContains(pagina, 'Parte posterior')
        self.assertContains(pagina, 'data-requires-back="false"')
        self.assertEqual(reverso.status_code, 400)
        self.assertEqual(frente.status_code, 200)
        self.assertIn('STUDENT_ID_FRONT', requisitos)
        self.assertNotIn('STUDENT_ID_BACK', requisitos)

    def test_repetir_captura_reemplaza_y_deja_una_activa(self):
        self._autorizar_captura_movil()
        self._capturar('frente', jpeg(marca=b'primera'))
        anterior = self.solicitud.documentos.get(
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            activo=True,
        )

        sin_confirmar = self._capturar('frente', jpeg(marca=b'segunda'))
        respuesta = self.client.post(
            self.url_camara,
            {
                'lado': 'frente',
                'captura': jpeg(marca=b'segunda'),
                'confirmar_reemplazo': '1',
            },
        )

        self.assertEqual(sin_confirmar.status_code, 409)
        self.assertEqual(respuesta.status_code, 200)
        anterior.refresh_from_db()
        self.assertFalse(anterior.activo)
        actual = self.solicitud.documentos.get(
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            activo=True,
        )
        self.assertEqual(actual.reemplaza_a, anterior)
        self.assertEqual(actual.origen_captura, OrigenCapturaDocumento.CAMERA)

    def test_camara_rechaza_pdf_y_servicio_rechaza_carga_convencional(self):
        self._autorizar_captura_movil()

        respuesta = self._capturar('frente', pdf())

        self.assertEqual(respuesta.status_code, 400)
        with self.assertRaises(ValidationError):
            registrar_documento(
                solicitud=self.solicitud,
                participante=self.estudiante,
                tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
                archivo=jpeg(),
                actor=self.usuario,
            )

    def test_camara_protege_sesion_idor_y_csrf_origin_null(self):
        self.assertEqual(self.client.get(self.url_camara).status_code, 302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.url_camara).status_code, 404)

        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.usuario)
        respuesta = csrf.post(
            self.url_camara,
            {'lado': 'frente', 'captura': jpeg()},
            HTTP_ORIGIN='null',
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_identificacion_no_se_reemplaza_por_ruta_de_archivos(self):
        self._autorizar_captura_movil()
        self._capturar('frente', jpeg())
        documento = self.solicitud.documentos.get(
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            activo=True,
        )
        url = reverse(
            'financiacion_educativa_web:documento-reemplazar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'documento_id': documento.pk,
            },
        )

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(url, {'archivo': jpeg(marca=b'otra')}).status_code,
            404,
        )

    def test_adulto_debe_aportar_certificado_del_estudiante_deudor(self):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=self.estudiante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf(),
            actor=self.usuario,
        )
        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }

        self.assertEqual(documento.participante, self.estudiante)
        self.assertTrue(documento.activo)
        self.assertTrue(requisitos['INCOME_CERTIFICATE'].cumplido)
        self.assertEqual(documento.estado_validacion, 'PENDING')

    def test_certificado_permite_reemplazo_trazable_y_vuelve_a_pendiente(self):
        anterior = registrar_documento(
            solicitud=self.solicitud,
            participante=self.estudiante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf(marca=b'primero'),
            actor=self.usuario,
        )

        actual = reemplazar_documento(
            documento=anterior,
            archivo=pdf(marca=b'segundo'),
            actor=self.usuario,
        )

        anterior.refresh_from_db()
        self.assertFalse(anterior.activo)
        self.assertTrue(actual.activo)
        self.assertEqual(actual.reemplaza_a, anterior)
        self.assertEqual(actual.estado_validacion, 'PENDING')

    def test_menor_exige_camara_del_tutor_y_su_certificado(self):
        solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='REF-MENOR',
            usuario=self.usuario,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        self.solicitud = solicitud
        self._crear_participante(
            nacimiento=date(2012, 1, 1),
            roles={RolParticipante.STUDENT},
            numero='1000200040',
        )
        tutor = self._crear_participante(
            nacimiento=date(1980, 1, 1),
            roles={RolParticipante.GUARDIAN, RolParticipante.PRINCIPAL_DEBTOR},
            numero='1000200050',
            relacion=RelacionEstudiante.MOTHER,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=tutor,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf(marca=b'tutor'),
            actor=self.usuario,
        )

        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(solicitud)
        }

        self.assertTrue(requisitos['INCOME_CERTIFICATE'].cumplido)
        self.assertIn('GUARDIAN_ID_FRONT', requisitos)
        self.assertIn('GUARDIAN_ID_BACK', requisitos)
        self.assertFalse(requisitos['GUARDIAN_ID_FRONT'].cumplido)
        self.assertFalse(requisitos['GUARDIAN_ID_BACK'].cumplido)

    def test_certificado_rechaza_participante_que_no_es_deudor(self):
        self.estudiante.roles.filter(
            rol=RolParticipante.PRINCIPAL_DEBTOR
        ).delete()

        with self.assertRaises(ValidationError):
            registrar_documento(
                solicitud=self.solicitud,
                participante=self.estudiante,
                tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
                archivo=pdf(),
                actor=self.usuario,
            )
