from datetime import date
from io import StringIO
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.storage import Storage
from django.core.management import call_command, CommandError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from instituciones.models import (
    CredencialAPIInstitucion,
    Institucion,
    MembresiaInstitucion,
)

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoProcesoAutomatizacionEducativa,
    EstadoValidacionIADocumento,
    OrigenCapturaDocumento,
    OrigenValidacionIADocumento,
    TipoArtefactoContractualEducativo,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    ConfiguracionFinancieraEducativa,
    CondicionesFinancieras,
    CuotaAmortizacionEducativa,
    DocumentoFinanciacion,
    OutboxCorreoEducativo,
    ParticipanteFinanciacion,
    ProcesoAutomatizacionEducativa,
    RegistroIdempotenciaSolicitud,
    SolicitudFinanciacionEducativa,
    ValidacionIADocumento,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_institucion,
    crear_solicitud,
)


class StoragePrivadoFalso(Storage):
    def __init__(self):
        self.archivos = {}
        self.eliminados = []
        self.fallar_al_eliminar = set()

    def exists(self, name):
        return name in self.archivos

    def size(self, name):
        if name not in self.archivos:
            raise FileNotFoundError(name)
        return len(self.archivos[name])

    def delete(self, name):
        if name in self.fallar_al_eliminar:
            raise OSError('fallo de storage simulado')
        self.eliminados.append(name)
        self.archivos.pop(name, None)


@override_settings(DEPLOYMENT_ENVIRONMENT='local')
class LimpiarSolicitudesEducativasInstitucionTests(TransactionTestCase):
    def setUp(self):
        self.storage = StoragePrivadoFalso()
        self.parches_storage = []
        for modelo, campo in (
            (DocumentoFinanciacion, 'archivo'),
            (ArtefactoContractualEducativo, 'archivo'),
            (ArtefactoContractualEducativo, 'archivo_firmado'),
        ):
            parche = mock.patch.object(
                modelo._meta.get_field(campo),
                'storage',
                self.storage,
            )
            parche.start()
            self.parches_storage.append(parche)
            self.addCleanup(parche.stop)
        self.institucion = crear_institucion('801')
        self.otra_institucion = crear_institucion('802')
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='limpieza@example.test',
            email='limpieza@example.test',
            password='Clave-2026',
        )
        self.grupo = Group.objects.create(name='Grupo preservado limpieza')
        self.permiso = Permission.objects.get(
            content_type__app_label='financiacion_educativa',
            codename='view_solicitudfinanciacioneducativa',
        )
        self.usuario.groups.add(self.grupo)
        self.usuario.user_permissions.add(self.permiso)
        self.membresia = MembresiaInstitucion.objects.create(
            usuario=self.usuario,
            institucion=self.institucion,
            rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
        )
        self.credencial = CredencialAPIInstitucion(
            institucion=self.institucion,
            nombre='Credencial preservada',
            prefijo_clave='limpieza_test',
        )
        self.credencial.establecer_secreto('secreto-solo-prueba')
        self.credencial.save()
        self.configuracion = crear_configuracion_financiera(version=801)
        self.solicitud = crear_solicitud(
            institucion=self.institucion,
            referencia='LIMPIEZA-001',
            usuario=self.usuario,
            correo='limpieza@example.test',
        )
        self.participante = ParticipanteFinanciacion.objects.create(
            solicitud=self.solicitud,
            nombres='PERSONA',
            apellidos='PRUEBA',
            tipo_documento='CC',
            numero_documento='1000801',
            usuario=self.usuario,
        )
        self.nombre_archivo = (
            'financiacion_educativa/documentos/limpieza/documento.pdf'
        )
        self.storage.archivos[self.nombre_archivo] = b'%PDF-1.4\nprueba'
        self.documento = DocumentoFinanciacion.objects.create(
            solicitud=self.solicitud,
            participante=self.participante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            archivo=self.nombre_archivo,
            nombre_original='documento.pdf',
            content_type='application/pdf',
            cargado_por=self.usuario,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
        )
        self.validacion = ValidacionIADocumento.objects.create(
            documento=self.documento,
            numero=1,
            estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
        )
        self.proceso = ProcesoAutomatizacionEducativa.objects.create(
            solicitud=self.solicitud,
            version_expediente=1,
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        )
        self.outbox = OutboxCorreoEducativo.objects.create(
            solicitud=self.solicitud,
            tipo_evento='DOSSIER_RECEIVED',
            clave_idempotencia='limpieza-outbox-001',
            evento_logico='a' * 64,
            destinatarios=['destino@example.test'],
            codigo_mensaje='DOSSIER_RECEIVED',
            message_id='limpieza-message-001',
        )
        self.idempotencia = RegistroIdempotenciaSolicitud.objects.create(
            institucion=self.institucion,
            solicitud=self.solicitud,
            clave_hash='b' * 64,
            payload_hash='c' * 64,
        )

    @property
    def nombre_base(self):
        return str(connection.settings_dict.get('NAME') or '')

    def _ejecutar_comando(
        self,
        *,
        institucion=None,
        execute=False,
        expected_count=1,
        confirm=None,
        expected_database=None,
        dry_run=False,
    ):
        institucion = institucion or self.institucion
        argumentos = [
            '--institucion-id',
            str(institucion.pk),
        ]
        if dry_run:
            argumentos.append('--dry-run')
        if execute:
            argumentos.append('--execute')
        if expected_count is not None:
            argumentos.extend(['--expected-count', str(expected_count)])
        if confirm is not None:
            argumentos.extend(['--confirm', str(confirm)])
        if expected_database is not None:
            argumentos.extend(['--expected-database', expected_database])
        salida = StringIO()
        errores = StringIO()
        call_command(
            'limpiar_solicitudes_educativas_institucion',
            *argumentos,
            stdout=salida,
            stderr=errores,
        )
        return salida.getvalue(), errores.getvalue()

    def _ejecutar_confirmado(self, **kwargs):
        return self._ejecutar_comando(
            execute=True,
            expected_count=kwargs.pop('expected_count', 1),
            confirm=kwargs.pop('confirm', self.institucion.pk),
            expected_database=kwargs.pop(
                'expected_database',
                self.nombre_base,
            ),
            **kwargs,
        )

    def test_dry_run_predeterminado_informa_y_no_modifica(self):
        salida, _ = self._ejecutar_comando(expected_count=None)
        self.assertIn('MODO=DRY_RUN', salida)
        self.assertIn('SOLICITUDES=1', salida)
        self.assertIn('SOLICITUD=LIMPIEZA-001|ESTADO=', salida)
        self.assertIn('ARCHIVOS_PRIVADOS=1', salida)
        self.assertIn('PRESERVAR_CREDENCIALES=1', salida)
        self.assertIn('PRESERVAR_MEMBRESIAS=1', salida)
        self.assertIn('RESULTADO=SIN_CAMBIOS', salida)
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )
        self.assertEqual(self.storage.eliminados, [])

    def test_institucion_inexistente_es_rechazada(self):
        with self.assertRaisesMessage(CommandError, 'no existe'):
            self._ejecutar_comando(
                institucion=type('InstitucionId', (), {'pk': uuid4()})(),
                expected_count=None,
            )

    def test_base_incorrecta_es_rechazada(self):
        with self.assertRaisesMessage(CommandError, 'base activa no coincide'):
            self._ejecutar_confirmado(expected_database='otra_base')
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )

    def test_conteo_incorrecto_es_rechazado(self):
        with self.assertRaisesMessage(CommandError, 'cantidad actual'):
            self._ejecutar_confirmado(expected_count=2)

    def test_confirmacion_incorrecta_es_rechazada(self):
        with self.assertRaisesMessage(CommandError, '--confirm debe coincidir'):
            self._ejecutar_confirmado(confirm=uuid4())

    def test_execute_exige_todas_las_confirmaciones(self):
        with self.assertRaisesMessage(CommandError, 'ejecucion requiere'):
            self._ejecutar_comando(
                execute=True,
                expected_count=None,
                confirm=None,
                expected_database=None,
            )

    def test_sin_execute_permanece_en_dry_run_aun_con_confirmaciones(self):
        salida, _ = self._ejecutar_comando(
            expected_count=1,
            confirm=self.institucion.pk,
            expected_database=self.nombre_base,
        )
        self.assertIn('MODO=DRY_RUN', salida)
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )

    def test_ejecucion_elimina_dependencias_y_preserva_identidad_institucional(self):
        usuario_id = self.usuario.pk
        credencial_estado = self.credencial.activa
        salida, _ = self._ejecutar_confirmado()
        self.assertIn('RESULTADO=COMPLETADO', salida)
        self.assertFalse(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )
        self.assertFalse(
            DocumentoFinanciacion.objects.filter(pk=self.documento.pk).exists()
        )
        self.assertFalse(
            ValidacionIADocumento.objects.filter(pk=self.validacion.pk).exists()
        )
        self.assertFalse(
            ProcesoAutomatizacionEducativa.objects.filter(
                pk=self.proceso.pk
            ).exists()
        )
        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(pk=self.outbox.pk).exists()
        )
        self.assertFalse(
            RegistroIdempotenciaSolicitud.objects.filter(
                pk=self.idempotencia.pk
            ).exists()
        )
        self.assertTrue(Institucion.objects.filter(pk=self.institucion.pk).exists())
        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.activa, credencial_estado)
        self.assertTrue(
            MembresiaInstitucion.objects.filter(pk=self.membresia.pk).exists()
        )
        self.assertTrue(get_user_model().objects.filter(pk=usuario_id).exists())
        self.assertTrue(
            self.usuario.groups.filter(pk=self.grupo.pk).exists()
        )
        self.assertTrue(
            self.usuario.user_permissions.filter(pk=self.permiso.pk).exists()
        )
        self.assertTrue(
            ConfiguracionFinancieraEducativa.objects.filter(
                pk=self.configuracion.pk
            ).exists()
        )

    def test_eliminacion_fisica_de_archivo_es_individual_y_post_commit(self):
        salida, _ = self._ejecutar_confirmado()
        self.assertEqual(self.storage.eliminados, [self.nombre_archivo])
        self.assertFalse(self.storage.exists(self.nombre_archivo))
        self.assertIn('ARCHIVOS_ELIMINADOS=1', salida)

    def test_elimina_fotografia_cuotas_y_archivos_contractuales(self):
        fotografia = crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 9, 1),
            configuracion=self.configuracion,
            actor=self.usuario,
            bloquear=True,
        )
        archivo_pagare = (
            'financiacion_educativa/contractuales/limpieza/pagare.pdf'
        )
        archivo_firmado = (
            'financiacion_educativa/contractuales/limpieza/pagare-firmado.pdf'
        )
        self.storage.archivos[archivo_pagare] = b'%PDF-1.4\npagare'
        self.storage.archivos[archivo_firmado] = b'%PDF-1.4\nfirmado'
        artefacto = ArtefactoContractualEducativo.objects.create(
            solicitud=self.solicitud,
            fotografia_financiera=fotografia,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            numero_version=1,
            estado=EstadoArtefactoContractualEducativo.SIGNED,
            numero_documento='PAGARE-LIMPIEZA-001',
            version_plantilla='TEST-V1',
            archivo=archivo_pagare,
            hash_sha256='d' * 64,
            tamano_bytes=len(self.storage.archivos[archivo_pagare]),
            archivo_firmado=archivo_firmado,
            hash_firmado_sha256='e' * 64,
            tamano_firmado_bytes=len(
                self.storage.archivos[archivo_firmado]
            ),
            firmado_en=timezone.now(),
            generado_por=self.usuario,
        )

        salida, _ = self._ejecutar_confirmado()

        self.assertFalse(
            ArtefactoContractualEducativo.objects.filter(
                pk=artefacto.pk
            ).exists()
        )
        self.assertFalse(
            CondicionesFinancieras.objects.filter(pk=fotografia.pk).exists()
        )
        self.assertFalse(
            CuotaAmortizacionEducativa.objects.filter(
                fotografia_id=fotografia.pk
            ).exists()
        )
        self.assertCountEqual(
            self.storage.eliminados,
            [self.nombre_archivo, archivo_pagare, archivo_firmado],
        )
        self.assertIn('ARCHIVOS_ELIMINADOS=3', salida)

    def test_fallo_de_storage_se_reporta_sin_ocultar_commit(self):
        self.storage.fallar_al_eliminar.add(self.nombre_archivo)
        salida, errores = self._ejecutar_confirmado()
        self.assertIn('ARCHIVOS_NO_ELIMINADOS=1', salida)
        self.assertIn('ERROR=OSError', errores)
        self.assertFalse(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )
        self.assertTrue(self.storage.exists(self.nombre_archivo))

    def test_relacion_protegida_revierte_toda_la_transaccion(self):
        protegido = ProtectedError('bloqueado', {self.documento})
        with mock.patch(
            'financiacion_educativa.services.limpieza_solicitudes._eliminar_dependencia',
            side_effect=protegido,
        ):
            with self.assertRaisesMessage(CommandError, 'relacion protegida'):
                self._ejecutar_confirmado()
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(pk=self.documento.pk).exists()
        )
        self.assertEqual(self.storage.eliminados, [])

    def test_cambio_concurrente_del_conteo_se_revalida_bajo_lock(self):
        ids_cambiados = (self.solicitud.pk, uuid4())
        with mock.patch(
            'financiacion_educativa.services.limpieza_solicitudes._bloquear_ids_solicitudes',
            return_value=ids_cambiados,
        ):
            with self.assertRaisesMessage(CommandError, 'cantidad de solicitudes cambio'):
                self._ejecutar_confirmado()
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )

    @override_settings(DEPLOYMENT_ENVIRONMENT='production')
    def test_produccion_esta_prohibida_incluso_en_dry_run(self):
        with self.assertRaisesMessage(CommandError, 'prohibido en produccion'):
            self._ejecutar_comando(expected_count=None)

    def test_una_institucion_no_afecta_solicitudes_de_otra(self):
        otra = crear_solicitud(
            institucion=self.otra_institucion,
            referencia='LIMPIEZA-AJENA',
            correo='ajena@example.test',
        )
        documento_ajeno = DocumentoFinanciacion.objects.create(
            solicitud=otra,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            archivo=self.nombre_archivo,
            nombre_original='compartido.pdf',
            content_type='application/pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
        )
        salida, _ = self._ejecutar_confirmado()
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(pk=otra.pk).exists()
        )
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(pk=documento_ajeno.pk).exists()
        )
        self.assertTrue(self.storage.exists(self.nombre_archivo))
        self.assertIn('ARCHIVOS_PRESERVADOS=1', salida)

    def test_nombre_de_archivo_inseguro_bloquea_execute(self):
        self.documento.archivo.name = '../fuera.pdf'
        self.documento.save(update_fields=['archivo', 'actualizado_en'])
        with self.assertRaisesMessage(CommandError, 'nombres de archivo no seguros'):
            self._ejecutar_confirmado()
        self.assertTrue(
            SolicitudFinanciacionEducativa.objects.filter(
                pk=self.solicitud.pk
            ).exists()
        )
