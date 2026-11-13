import json
import threading
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection, connections, close_old_connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature, Client
from django.urls import reverse

from financiacion_educativa.models import ArtefactoContractualEducativo
from financiacion_educativa.models_libro import PoliticaLibroEducativo, MovimientoLibroEducativo
from financiacion_educativa.models_gobernanza_libro import (
    PropuestaPoliticaLibro, EventoPoliticaLibro, ControlPilotoLibro, EventoControlPilotoLibro,
)
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services import libro_pagos as libro, gobernanza_libro as gobierno
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.tests.test_libro_pagos import DatosLibro
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests.piloto_libro_helpers import conceder_alcances, politica_sintetica


class DatosGobierno(DatosLibro):
    def propuesta(self, version='PILOTO_V2', institucion=None, desde=None, hasta=None, sintetica=True):
        institucion = institucion or self.institucion
        conceder_alcances(institucion, self.actor, self.actor)
        conceder_alcances(institucion, self.aprobador, self.actor)
        return gobierno.preparar_politica(institucion_id=institucion.pk, version=version,
            reglas=dict(libro.REGLAS_SINTETICAS), vigente_desde=desde or date(2026, 7, 1), vigente_hasta=hasta,
            es_sintetica=sintetica, respaldo='Documento sintetico sin validez comercial', actor=self.actor)

    def revisar(self, propuesta):
        return gobierno.revisar_politica(propuesta_id=propuesta.pk, actor=self.aprobador, respaldo='Revision independiente sintetica')

    def aprobar(self, propuesta, **kwargs):
        return gobierno.aprobar_politica(propuesta_id=propuesta.pk, actor=self.aprobador,
            respaldo='Aprobacion sintetica de ensayo', **kwargs)

    def post(self, ingreso, token):
        return libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token,
            clave=uuid4(), motivo='Confirmacion sintetica', confirmar=True)


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class GobernanzaLibroTests(DatosGobierno, TestCase):
    def test_propuesta_revision_aprobacion_y_replay(self):
        p = self.propuesta(institucion=self.otra)
        self.assertEqual(self.propuesta(institucion=self.otra).pk, p.pk)
        with self.assertRaises(ValidationError):
            self.aprobar(p)
        revision = self.revisar(p)
        self.assertEqual(self.revisar(p).pk, revision.pk)
        politica = self.aprobar(p)
        self.assertEqual(self.aprobar(p).pk, politica.pk)
        self.assertEqual(politica.aprobada_por, self.aprobador)
        self.assertEqual(politica.propuesta.preparada_por, self.actor)
        self.assertEqual(set(p.eventos.values_list('tipo', flat=True)), {'REVIEWED', 'APPROVED'})

    def test_preparador_no_puede_revisar_ni_aprobar_su_propuesta(self):
        p = self.propuesta()
        with self.assertRaises(PermissionDenied):
            gobierno.revisar_politica(propuesta_id=p.pk, actor=self.actor, respaldo='Intento propio')
        self.revisar(p)
        with self.assertRaises(PermissionDenied):
            gobierno.aprobar_politica(propuesta_id=p.pk, actor=self.actor, respaldo='Intento propio')

    def test_rechazada_no_se_aprueba_y_todo_registro_es_inmutable(self):
        p = self.propuesta()
        gobierno.revisar_politica(propuesta_id=p.pk, actor=self.aprobador, respaldo='Falta evidencia', aceptar=False)
        with self.assertRaises(ValidationError):
            self.aprobar(p)
        for llamada in (p.save, p.delete, lambda: PropuestaPoliticaLibro.objects.update(reglas={}),
                        lambda: EventoPoliticaLibro.objects.all().delete()):
            with self.assertRaises(ValidationError):
                llamada()

    def test_solape_inclusivo_y_periodos_adyacentes(self):
        a = self.propuesta('JULIO', self.otra, hasta=date(2026, 7, 31))
        self.revisar(a)
        julio = self.aprobar(a)
        b = self.propuesta('SOLAPE', self.otra, desde=date(2026, 7, 31), hasta=date(2026, 8, 31))
        self.revisar(b)
        with self.assertRaises(ValidationError):
            self.aprobar(b)
        c = self.propuesta('AGOSTO', self.otra, desde=date(2026, 8, 1))
        self.revisar(c)
        agosto = self.aprobar(c)
        self.assertEqual(gobierno.resolver_politica(self.otra.pk, date(2026, 7, 31)).pk, julio.pk)
        self.assertEqual(gobierno.resolver_politica(self.otra.pk, date(2026, 8, 1)).pk, agosto.pk)

    def test_reemplazo_conserva_historia_y_no_hay_dos_aplicables(self):
        movimiento = self.registrar(self.nuevo_ingreso())
        anterior = PoliticaLibroEducativo.objects.values().get(pk=self.politica.pk)
        p = self.propuesta()
        self.revisar(p)
        nueva = self.aprobar(p, reemplaza_id=self.politica.pk)
        self.assertEqual(PoliticaLibroEducativo.objects.values().get(pk=self.politica.pk), anterior)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.politica_id, self.politica.pk)
        self.assertEqual(gobierno.resolver_politica(self.institucion.pk, self.fecha).pk, nueva.pk)
        self.assertEqual(gobierno.politicas_vigentes(self.institucion.pk, self.fecha).count(), 1)
        self.assertTrue(self.politica.propuesta.eventos.filter(tipo='RETIRED').exists())

    def test_previa_anterior_a_cambio_politica_es_rechazada(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        p = self.propuesta()
        self.revisar(p)
        self.aprobar(p, reemplaza_id=self.politica.pk)
        with self.assertRaisesMessage(ValidationError, 'politica cambio'):
            self.post(ingreso, token)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_apagado_y_reactivacion_invalidan_previa_por_revision(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=False, actor=self.aprobador, motivo='Apagado sintetico')
        with self.assertRaises(ValidationError):
            self.post(ingreso, token)
        gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=True, actor=self.aprobador, motivo='Reactivacion sintetica')
        with self.assertRaisesMessage(ValidationError, 'cambio'):
            self.post(ingreso, token)

    def test_flag_piloto_y_alcance_son_independientes(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        with override_settings(FINANCIACION_EDUCATIVA_LIBRO_ENABLED=False), self.assertRaises(ValidationError):
            self.post(ingreso, token)
        gobierno.asignar_alcance(institucion_id=self.institucion.pk, usuario_id=self.actor.pk,
            accion='POST', habilitado=False, actor=self.aprobador, motivo='Revocacion de prueba')
        with self.assertRaises(PermissionDenied):
            self.post(ingreso, token)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_sintetica_bloqueada_en_produccion_aun_con_flag_y_control_activos(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        with override_settings(DEPLOYMENT_ENVIRONMENT='production'):
            with self.assertRaises(ValidationError):
                self.post(ingreso, token)
            with self.assertRaises(ValidationError):
                self.propuesta('NO_PRODUCTION')
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_politica_no_sintetica_exige_aceptacion_comercial_explicita(self):
        p = self.propuesta('ACK_TEST_ONLY', sintetica=False)
        self.revisar(p)
        with self.assertRaises(ValidationError):
            self.aprobar(p, reemplaza_id=self.politica.pk)
        politica = self.aprobar(p, reemplaza_id=self.politica.pk, aceptar_uso_real=True)
        # Production *setting* in a disposable test DB; never a production server.
        with override_settings(DEPLOYMENT_ENVIRONMENT='production'):
            self.assertEqual(gobierno.resolver_politica(self.institucion.pk, self.fecha).pk, politica.pk)

    def test_historica_sin_metadatos_no_se_habilita_ni_reclasifica(self):
        historica = PoliticaLibroEducativo.objects.create(institucion=self.otra, version='LEGACY',
            reglas=dict(libro.REGLAS_SINTETICAS), referencia_aprobacion='Referencia anterior',
            aprobada_por=self.actor, vigente_desde=date(2026, 1, 1))
        self.assertIsNone(historica.es_sintetica)
        with self.assertRaises(ValidationError):
            gobierno.resolver_politica(self.otra.pk, self.fecha, historica.pk)
        historica.refresh_from_db()
        self.assertIsNone(historica.propuesta_id)

    def test_no_habilita_dos_instituciones_y_fecha_bancaria_no_fecha_registro(self):
        otra = politica_sintetica(self.otra, self.actor, self.aprobador, 'OTHER_TEST', date(2026, 7, 1))
        with self.assertRaises(ValidationError):
            gobierno.controlar_piloto(institucion_id=self.otra.pk, activo=True, actor=self.aprobador, motivo='Intento simultaneo')
        ingreso = self.nuevo_ingreso()
        self.assertNotEqual(ingreso.fecha_efectiva, ingreso.confirmada_en.date())
        self.assertEqual(gobierno.resolver_politica(self.institucion.pk, ingreso.fecha_efectiva).pk, self.politica.pk)
        self.assertNotEqual(otra.pk, self.politica.pk)

    def test_permiso_sin_alcance_y_alcance_sin_permiso_no_bastan(self):
        otro = get_user_model().objects.create_user('operador-piloto', 'operador-piloto@example.test', 'synthetic-only')
        codigos = ['acceder_dashboard_operativo', 'consultar_recaudos_educativos', 'aplicar_cuotas_educativas']
        otro.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa', codename__in=codigos))
        with self.assertRaises(PermissionDenied):
            libro.preparar_preview(ingreso_id=self.nuevo_ingreso().pk, actor=otro, modo='PARTIAL', politica_id=self.politica.pk)
        conceder_alcances(self.institucion, otro, self.actor, ['POST'])
        ingreso = self.nuevo_ingreso()
        _, token = libro.preparar_preview(ingreso_id=ingreso.pk, actor=otro, modo='PARTIAL', politica_id=self.politica.pk)
        otro.user_permissions.remove(Permission.objects.get(content_type__app_label='financiacion_educativa', codename='aplicar_cuotas_educativas'))
        with self.assertRaises(PermissionDenied):
            libro.contabilizar(ingreso_id=ingreso.pk, actor=otro, token=token, clave=uuid4(), motivo='Sin permiso actual', confirmar=True)

    def test_compensacion_exige_autorizante_distinto_y_no_necesita_permiso_post(self):
        m = self.registrar(self.nuevo_ingreso())
        with self.assertRaises(PermissionDenied):
            libro.preparar_compensacion(movimiento_id=m.pk, actor=self.actor)
        autorizante = get_user_model().objects.create_user('compensador', 'compensador@example.test', 'synthetic-only')
        codigos = ['acceder_dashboard_operativo', 'consultar_recaudos_educativos', 'compensar_libro_educativo']
        autorizante.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa', codename__in=codigos))
        conceder_alcances(self.institucion, autorizante, self.actor, ['COMPENSATE'])
        datos = self.datos_compensacion(m, actor=autorizante)
        compensacion = libro.compensar(**datos)
        self.assertEqual(compensacion.actor_id, autorizante.pk)
        self.assertFalse(autorizante.has_perm(gobierno.PERMISOS['POST']))

    def test_revocacion_consulta_no_se_omite_por_cache_del_actor(self):
        operador = get_user_model().objects.create_user('consulta-piloto', 'consulta-piloto@example.test', 'synthetic-only')
        codigos = ['acceder_dashboard_operativo', 'consultar_recaudos_educativos', 'aplicar_cuotas_educativas']
        operador.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa', codename__in=codigos))
        conceder_alcances(self.institucion, operador, self.actor, ['POST'])
        ingreso = self.nuevo_ingreso()
        _, token = libro.preparar_preview(ingreso_id=ingreso.pk, actor=operador, modo='PARTIAL', politica_id=self.politica.pk)
        self.assertTrue(operador.has_perm('financiacion_educativa.consultar_recaudos_educativos'))
        operador.user_permissions.remove(Permission.objects.get(content_type__app_label='financiacion_educativa', codename='consultar_recaudos_educativos'))
        with self.assertRaises(PermissionDenied):
            libro.contabilizar(ingreso_id=ingreso.pk, actor=operador, token=token,
                clave=uuid4(), motivo='Consulta revocada despues de previsualizar', confirmar=True)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_compensacion_previa_vencida_no_contabiliza(self):
        m = self.registrar(self.nuevo_ingreso())
        datos = self.datos_compensacion(m)
        with patch('django.core.signing.time.time', return_value=9999999999), self.assertRaises(ValidationError):
            libro.compensar(**datos)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    def test_token_compensacion_no_es_una_vista_previa_de_aplicacion(self):
        m = self.registrar(self.nuevo_ingreso())
        datos = self.datos_compensacion(m)
        ingreso = self.nuevo_ingreso()
        with self.assertRaises(ValidationError):
            libro.contabilizar(ingreso_id=ingreso.pk, actor=self.aprobador, token=datos['token'],
                clave=uuid4(), motivo='Tipo de token incorrecto', confirmar=True)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    def test_sello_gobernanza_persistido_y_no_solo_hash(self):
        m = self.registrar(self.nuevo_ingreso())
        sello = m.estado_despues['gobernanza']
        self.assertEqual(sello['politica_id'], self.politica.pk)
        self.assertEqual(sello['revision_piloto'], ControlPilotoLibro.objects.get(pk=1).revision)
        self.assertEqual(sello['operacion'], 'POST')
        compensacion = libro.compensar(**self.datos_compensacion(m))
        self.assertEqual(compensacion.estado_despues['gobernanza']['operacion'], 'COMPENSATE')

    def test_apagado_conserva_consulta_tablero_y_evidencia(self):
        ingreso = self.nuevo_ingreso()
        m = self.registrar(ingreso)
        antes = list(MovimientoLibroEducativo.objects.values())
        gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=False, actor=self.aprobador, motivo='Ensayo de apagado')
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:libro-ingreso', args=[ingreso.pk])).status_code, 200)
        self.assertEqual(list(MovimientoLibroEducativo.objects.values()), antes)
        with self.assertRaises(ValidationError):
            self.registrar(self.nuevo_ingreso())
        self.assertTrue(MovimientoLibroEducativo.objects.filter(pk=m.pk).exists())

    def test_acceso_politicas_por_url_y_csrf(self):
        p = self.propuesta()
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:operaciones:politica-detalle', args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.actor)
        self.assertContains(self.client.get(url), 'Fecha bancaria')
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.aprobador)
        self.assertEqual(cliente.post(url, {'accion': 'REVIEW', 'respaldo': 'CSRF', 'confirmar': 'on'}).status_code, 403)

    def test_alcance_una_institucion_no_habilita_otra_por_url(self):
        propuesta = self.propuesta('OTHER_SCOPE', self.otra)
        operador = get_user_model().objects.create_user('scope-piloto', 'scope@example.test', 'synthetic-only')
        operador.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa',
            codename__in=['acceder_dashboard_operativo', 'aplicar_cuotas_educativas', 'consultar_recaudos_educativos']))
        conceder_alcances(self.institucion, operador, self.actor, ['POST'])
        self.client.force_login(operador)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:politica-detalle', args=[propuesta.pk])).status_code, 403)
        pagina = self.client.get(reverse('financiacion_educativa_web:operaciones:politicas-libro'), {'institucion': self.otra.pk})
        self.assertNotContains(pagina, propuesta.version)
        self.assertNotContains(pagina, self.otra.nombre_comercial)


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class ConcurrenciaGobiernoTests(DatosGobierno, TransactionTestCase):
    from financiacion_educativa.tests.test_recaudos_breb import ConcurrenciaRecaudosTests as _base
    paralelos = _base.paralelos

    @skipUnlessDBFeature('has_select_for_update')
    def test_aprobaciones_concurrentes_no_solapan_intervalos(self):
        a = self.propuesta('CONCURRENT_A', self.otra)
        b = self.propuesta('CONCURRENT_B', self.otra)
        self.revisar(a)
        self.revisar(b)
        resultados, errores = self.paralelos([lambda: self.aprobar(a), lambda: self.aprobar(b)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(gobierno.politicas_vigentes(self.otra.pk, self.fecha).count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_activaciones_solo_abren_una_institucion(self):
        politica_sintetica(self.otra, self.actor, self.aprobador, 'PILOT_OTHER', date(2026, 7, 1))
        gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=False, actor=self.aprobador, motivo='Preparacion de carrera')
        def activar(inst):
            return gobierno.controlar_piloto(institucion_id=inst.pk, activo=True, actor=self.aprobador, motivo='Activacion concurrente')
        resultados, errores = self.paralelos([lambda: activar(self.institucion), lambda: activar(self.otra)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertTrue(ControlPilotoLibro.objects.get(pk=1).activo)

    @skipUnlessDBFeature('has_select_for_update_nowait')
    def test_apagado_espera_escritura_en_curso_y_bloquea_siguientes(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        entro, liberar, intento_apagar = threading.Event(), threading.Event(), threading.Event()
        errores, hilos = [], []
        crear = MovimientoLibroEducativo.objects.create
        principal = connection.connection

        def crear_bloqueado(**datos):
            entro.set()
            if not liberar.wait(15):
                raise RuntimeError('No se libero el bloqueo de ensayo')
            return crear(**datos)

        def ejecutar(fn):
            close_old_connections()
            propia = connections['default']
            try:
                fn()
            except Exception as exc:
                errores.append(exc)
            finally:
                propia.close()

        def apagar():
            intento_apagar.set()
            gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=False,
                actor=self.aprobador, motivo='Apagado concurrente sintetico')

        try:
            with patch.object(MovimientoLibroEducativo.objects, 'create', side_effect=crear_bloqueado):
                a = threading.Thread(target=ejecutar, args=(lambda: self.post(ingreso, token),))
                hilos.append(a)
                a.start()
                self.assertTrue(entro.wait(15))
                b = threading.Thread(target=ejecutar, args=(apagar,))
                hilos.append(b)
                b.start()
                self.assertTrue(intento_apagar.wait(15))
                with self.assertRaises(DatabaseError), transaction.atomic():
                    ControlPilotoLibro.objects.select_for_update(nowait=True).get(pk=1)
                liberar.set()
                for hilo in hilos:
                    hilo.join(20)
                    self.assertFalse(hilo.is_alive())
        finally:
            liberar.set()
            for hilo in hilos:
                hilo.join(20)
        self.assertEqual(errores, [])
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)
        self.assertFalse(ControlPilotoLibro.objects.get(pk=1).activo)
        with self.assertRaises(ValidationError):
            self.registrar(self.nuevo_ingreso())
        self.assertIs(connection.connection, principal)
        self.assertTrue(connection.is_usable())

    def test_diagnostico_es_solo_lectura_sin_pii_y_cuadra(self):
        ingreso = self.nuevo_ingreso()
        self.registrar(ingreso)
        antes = list(MovimientoLibroEducativo.objects.values())
        salida = StringIO()
        call_command('diagnosticar_libro_2b', institucion_id=str(self.institucion.pk),
            desde='2026-07-01', hasta='2026-07-31', actor_id=self.actor.pk, stdout=salida)
        texto = salida.getvalue()
        datos = json.loads(texto)
        self.assertTrue(datos['solo_lectura'])
        self.assertTrue(datos['cuadra'])
        for prohibido in (self.pagador.email, self.actor.email, ingreso.referencia_verificada,
                          ingreso.evidencia_movimiento.archivo.name, 'token', 'https://'):
            self.assertNotIn(prohibido, texto)
        self.assertEqual(list(MovimientoLibroEducativo.objects.values()), antes)
