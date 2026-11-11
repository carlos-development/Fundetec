from decimal import Decimal
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models import ProcesoFirmaEducativa
from financiacion_educativa.models_recaudos import (
    AplicacionCuotaEducativa, ConciliacionRecaudoEducativo,
    ReporteRecaudoEducativo, IntentoEscaneoRecaudo,
)
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.tests import test_recaudos_breb as base


class DatosAplicacion(base.DatosRecaudosMixin):
    def setUp(self):
        self.preparar_recaudos()
        self.cuota = self.fotografia.cuotas.order_by('numero').first()
        self.vistas = {}

    def ingreso(self, monto=None, cuota=None, **datos):
        objetivo = cuota or self.cuota
        reporte, _ = self.reportar(monto=objetivo.valor_cuota if monto is None else monto, **datos)
        self.escanear(reporte.comprobante_id)
        recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte, referencia=uuid4().hex))
        self.vistas[reporte.pk] = signing.dumps(
            aplicacion_cuotas._snapshot(reporte, objetivo, self.actor),
            salt=aplicacion_cuotas.VISTA_PREVIA_SALT,
        )
        return reporte

    def aplicar(self, reporte, cuota=None):
        return aplicacion_cuotas.aplicar_cuota(
            reporte_id=reporte.pk, cuota_id=(cuota or self.cuota).pk, actor=self.actor,
            vista_previa=self.vistas.get(reporte.pk, ''), confirmar_cuota=True,
        )


@override_settings(**base.AISLAMIENTO)
class AplicacionCuotasTests(DatosAplicacion, TestCase):
    def operador(self, permisos):
        usuario = get_user_model().objects.create_user('aplicador-' + uuid4().hex, 'operador@example.test', 'only-test')
        usuario.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='financiacion_educativa',
            codename__in=[p.rsplit('.', 1)[1] for p in permisos],
        ))
        return usuario

    def datos_post(self, reporte):
        return {'cuota': self.cuota.pk, 'vista_previa': self.vistas[reporte.pk], 'confirmar_cuota': 'on'}

    def test_aplicacion_visible_en_operaciones_y_en_reporte_del_estudiante(self):
        reporte = self.ingreso()
        counts = ReporteRecaudoEducativo.objects.count(), ConciliacionRecaudoEducativo.objects.count()
        prefijo = 'financiacion_educativa_web:operaciones:'
        detalle = reverse(prefijo + 'recaudo-detalle', args=[reporte.pk])
        aplicar = reverse(prefijo + 'recaudo-aplicar-cuota', args=[reporte.pk])
        contrato = self.detalle_url(self.solicitud)
        self.client.force_login(self.actor)
        self.assertContains(self.client.get(detalle), 'Confirmar aplicación')
        self.assertEqual(self.client.get(aplicar).status_code, 405)
        datos = {'cuota': self.cuota.pk, 'vista_previa': self.vistas[reporte.pk], 'confirmar_cuota': 'on'}
        self.assertRedirects(self.client.post(aplicar, datos), detalle)
        self.assertRedirects(self.client.post(aplicar, datos), detalle)
        aplicacion = AplicacionCuotaEducativa.objects.get()
        self.assertEqual(aplicacion.capital_aplicado, self.cuota.capital)
        self.assertEqual(aplicacion.interes_aplicado, self.cuota.interes)
        self.assertEqual(aplicacion.fecha_efectiva, aplicacion.conciliacion.fecha_efectiva)
        self.assertEqual(aplicacion.actor, self.actor)
        self.assertTrue(aplicacion.cuota_verificada)
        self.assertContains(self.client.get(contrato), 'Pagada')
        self.assertEqual(
            self.client.get(contrato).context['capital_pendiente'],
            self.fotografia.capital_financiado - self.cuota.capital,
        )
        self.cuota.refresh_from_db()
        self.assertEqual(self.cuota.valor_cuota, aplicacion.conciliacion.monto_recibido)
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, reporte.pk])
        self.assertContains(self.client.get(url), 'Pago aplicado a la cuota')
        self.assertEqual(self.client.post(aplicar, datos).status_code, 403)
        self.assertEqual((ReporteRecaudoEducativo.objects.count(), ConciliacionRecaudoEducativo.objects.count()), counts)

    def test_no_aplica_monto_diferente_ingreso_no_conciliado_o_abono(self):
        distinto = self.ingreso(monto=self.cuota.valor_cuota - Decimal('1'))
        sin_conciliar, _ = self.reportar()
        abono = self.ingreso(proposito='PRINCIPAL')
        for reporte in (distinto, sin_conciliar, abono):
            with self.subTest(reporte=reporte.pk), self.assertRaises(ValidationError):
                self.aplicar(reporte)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_permiso_de_conciliacion_no_otorga_aplicacion(self):
        reporte = self.ingreso()
        operador = self.operador((recaudos.PERMISO_CONSULTAR, recaudos.PERMISO_CONCILIAR, aplicacion_cuotas.PERMISO_ACCESO))
        self.client.force_login(operador)
        detalle = reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[reporte.pk])
        aplicar = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[reporte.pk])
        self.assertNotContains(self.client.get(detalle), 'Confirmar aplicación')
        self.assertEqual(self.client.post(aplicar, self.datos_post(reporte)).status_code, 403)
        with self.assertRaises(PermissionDenied):
            aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                actor=operador, vista_previa=self.vistas[reporte.pk], confirmar_cuota=True)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_aplicador_puede_ser_distinto_del_conciliador_sin_permiso_conciliar(self):
        reporte = self.ingreso()
        operador = self.operador((recaudos.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_APLICAR, aplicacion_cuotas.PERMISO_ACCESO))
        self.assertFalse(operador.has_perm(recaudos.PERMISO_CONCILIAR))
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=reporte, actor=operador)
        app = aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=cuota.pk,
            actor=operador, vista_previa=token, confirmar_cuota=True)
        self.assertEqual(app.actor, operador)
        self.assertEqual(app.conciliacion.actor, self.actor)

    def test_permiso_aplicar_solo_staff_inactivo_y_pagador_no_bastan(self):
        reporte = self.ingreso()
        solo_aplicar = self.operador((aplicacion_cuotas.PERMISO_APLICAR,))
        staff = self.operador(())
        staff.is_staff = True
        staff.save()
        inactivo = self.operador((aplicacion_cuotas.PERMISO_APLICAR, aplicacion_cuotas.PERMISO_ACCESO, recaudos.PERMISO_CONSULTAR))
        inactivo.is_active = False
        inactivo.save()
        for actor in (solo_aplicar, staff, inactivo, self.pagador):
            with self.subTest(actor=actor.pk), self.assertRaises(PermissionDenied):
                aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                    actor=actor, vista_previa=self.vistas[reporte.pk], confirmar_cuota=True)

    def test_confirmacion_explicita_y_csrf_son_obligatorios(self):
        reporte = self.ingreso()
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[reporte.pk])
        datos = self.datos_post(reporte)
        datos.pop('confirmar_cuota')
        respuesta = self.client.post(url, datos)
        self.assertContains(respuesta, 'Debes verificar la intencion del estudiante')
        with self.assertRaises(ValidationError):
            aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                actor=self.actor, vista_previa=self.vistas[reporte.pk], confirmar_cuota=False)
        cliente_csrf = Client(enforce_csrf_checks=True)
        cliente_csrf.force_login(self.actor)
        self.assertEqual(cliente_csrf.post(url, self.datos_post(reporte)).status_code, 403)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_token_manipulado_vencido_o_de_otro_operador_no_aplica(self):
        reporte = self.ingreso()
        for token in ('', 'not-a-signed-preview', self.vistas[reporte.pk] + 'x'):
            with self.subTest(token_len=len(token)), self.assertRaises(ValidationError):
                aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                    actor=self.actor, vista_previa=token, confirmar_cuota=True)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 3600), self.assertRaises(ValidationError):
            self.aplicar(reporte)
        otro = self.operador((recaudos.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_APLICAR, aplicacion_cuotas.PERMISO_ACCESO))
        with self.assertRaises(ValidationError):
            aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                actor=otro, vista_previa=self.vistas[reporte.pk], confirmar_cuota=True)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_cuota_de_otra_institucion_y_token_de_otro_ingreso_fallan(self):
        reporte, otro_reporte = self.ingreso(), self.ingreso()
        _, foto_ajena, _, _ = self.contrato('OTRO-TENANT', institucion=self.otra)
        with self.assertRaises(ValidationError):
            self.aplicar(reporte, foto_ajena.cuotas.first())
        with self.assertRaises(ValidationError):
            aplicacion_cuotas.aplicar_cuota(reporte_id=otro_reporte.pk, cuota_id=self.cuota.pk,
                actor=self.actor, vista_previa=self.vistas[reporte.pk], confirmar_cuota=True)
        self.client.force_login(self.pagador)
        ajeno = reverse('financiacion_educativa_web:reporte-pago', args=[foto_ajena.solicitud_id, reporte.pk])
        self.assertEqual(self.client.get(ajeno).status_code, 404)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_revalida_seguridad_integridad_y_firma_al_confirmar(self):
        reporte = self.ingreso()
        evidencia = reporte.conciliacion.evidencia_movimiento
        with evidencia.archivo.open('rb') as archivo:
            bytes_originales = archivo.read()
        with evidencia.archivo.open('wb') as archivo:
            archivo.write(b'synthetic-altered-bank-evidence')
        with self.assertRaises(ValidationError):
            self.aplicar(reporte)
        with evidencia.archivo.open('wb') as archivo:
            archivo.write(bytes_originales)
        ProcesoFirmaEducativa.objects.filter(solicitud=self.solicitud).update(estado='SENT')
        with self.assertRaises(ValidationError):
            self.aplicar(reporte)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_nuevo_escaneo_pendiente_bloquea_sin_inventar_safe(self):
        reporte = self.ingreso()
        IntentoEscaneoRecaudo.objects.create(archivo=reporte.comprobante,
            numero=2, sha256=reporte.comprobante.sha256)
        with self.assertRaises(ValidationError):
            self.aplicar(reporte)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_calendario_foto_pagare_conciliacion_y_api_no_cambian(self):
        from financiacion_educativa.models import ArtefactoContractualEducativo
        from financiacion_educativa.services.estado_publico import obtener_resultado_publico
        reporte = self.ingreso()
        cuotas = list(self.fotografia.cuotas.values())
        fotografia = type(self.fotografia).objects.values().get(pk=self.fotografia.pk)
        contratos = list(ArtefactoContractualEducativo.objects.filter(solicitud=self.solicitud).values())
        conciliacion = ConciliacionRecaudoEducativo.objects.values().get(reporte=reporte)
        publico = obtener_resultado_publico(self.solicitud)
        with patch('requests.sessions.Session.request') as http:
            self.aplicar(reporte)
        http.assert_not_called()
        self.assertEqual(list(self.fotografia.cuotas.values()), cuotas)
        self.assertEqual(type(self.fotografia).objects.values().get(pk=self.fotografia.pk), fotografia)
        self.assertEqual(list(ArtefactoContractualEducativo.objects.filter(solicitud=self.solicitud).values()), contratos)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.values().get(reporte=reporte), conciliacion)
        self.solicitud.refresh_from_db()
        self.assertEqual(obtener_resultado_publico(self.solicitud), publico)
        self.assertEqual(self.solicitud.estado, 'APPROVED')

    def test_siguiente_ingreso_paga_siguiente_cuota_no_reusa_el_anterior(self):
        primero = self.ingreso()
        app = self.aplicar(primero)
        segunda = self.fotografia.cuotas.get(numero=self.cuota.numero + 1)
        siguiente = self.ingreso(cuota=segunda)
        cuota, _ = aplicacion_cuotas.preparar_vista_previa(reporte=siguiente, actor=self.actor)
        self.assertEqual(cuota.pk, segunda.pk)
        self.aplicar(siguiente, segunda)
        self.assertEqual(self.aplicar(primero).pk, app.pk)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 2)

    def test_evidencia_aplicada_es_inmutable_y_unica_en_base_de_datos(self):
        primero, segundo = self.ingreso(), self.ingreso()
        app = self.aplicar(primero)
        for operacion in (
            lambda: app.save(), lambda: app.delete(),
            lambda: AplicacionCuotaEducativa.objects.update(capital_aplicado=1),
            lambda: AplicacionCuotaEducativa._base_manager.filter(pk=app.pk).delete(),
            lambda: AplicacionCuotaEducativa.objects.bulk_update([app], ['capital_aplicado']),
            lambda: AplicacionCuotaEducativa.objects.bulk_create([app]),
        ):
            with self.assertRaises(ValidationError):
                operacion()
        for ingreso, cuota in ((app.conciliacion, self.fotografia.cuotas.get(numero=2)),
                               (segundo.conciliacion, self.cuota)):
            duplicado = AplicacionCuotaEducativa(conciliacion=ingreso, cuota=cuota,
                capital_aplicado=cuota.capital, interes_aplicado=cuota.interes,
                fecha_efectiva=ingreso.fecha_efectiva, actor=self.actor,
                cuota_verificada=True, creada_en=timezone.now())
            # Test-only bypass of full_clean to exercise the database unique keys.
            with self.assertRaises(IntegrityError), transaction.atomic():
                duplicado.save_base(raw=True, force_insert=True)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)

    def test_canal_deshabilitado_no_permite_aplicar(self):
        reporte = self.ingreso()
        with override_settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False), self.assertRaises(ValidationError):
            self.aplicar(reporte)

    def test_uuid_malformado_no_provoca_error_500(self):
        reporte = self.ingreso()
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[reporte.pk])
        respuesta = self.client.post(url, {**self.datos_post(reporte), 'cuota': 'not-a-uuid'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context['aplicar_form'].errors)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_diferencia_ya_conciliada_no_se_resuelve_aplicando(self):
        reporte, _ = self.reportar(monto=self.cuota.valor_cuota - Decimal('1'))
        self.escanear(reporte.comprobante_id)
        recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte,
            monto=self.cuota.valor_cuota, justificacion_diferencia='Diferencia sintetica documentada'))
        self.vistas[reporte.pk] = signing.dumps(
            aplicacion_cuotas._snapshot(reporte, self.cuota, self.actor), salt=aplicacion_cuotas.VISTA_PREVIA_SALT)
        with self.assertRaisesMessage(ValidationError, 'diferencia con el monto reportado'):
            self.aplicar(reporte)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_importes_o_vencimiento_de_vista_anterior_exigen_recarga(self):
        reporte = self.ingreso()
        payload = aplicacion_cuotas._snapshot(reporte, self.cuota, self.actor)
        for cambio in ({'capital': '0.01'}, {'vencimiento': '2000-01-01'}):
            # A formerly signed preview is represented without mutating the calendar.
            token = signing.dumps({**payload, **cambio}, salt=aplicacion_cuotas.VISTA_PREVIA_SALT)
            with self.assertRaisesMessage(ValidationError, 'Recarga el reporte'):
                aplicacion_cuotas.aplicar_cuota(reporte_id=reporte.pk, cuota_id=self.cuota.pk,
                    actor=self.actor, vista_previa=token, confirmar_cuota=True)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_sin_cuotas_pendientes_no_aplica_ni_cambia_approved(self):
        for cuota in self.fotografia.cuotas.order_by('numero'):
            ingreso = self.ingreso(cuota=cuota)
            self.aplicar(ingreso, cuota)
        adicional = self.ingreso()
        with self.assertRaisesMessage(ValidationError, 'No quedan cuotas pendientes'):
            self.aplicar(adicional)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), self.fotografia.cuotas.count())

    def test_una_vista_desactualizada_no_cobra_otra_cuota(self):
        primero, segundo = self.ingreso(), self.ingreso()
        self.aplicar(primero)
        with self.assertRaisesMessage(ValidationError, 'Recarga el reporte'):
            self.aplicar(segundo)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)


@override_settings(**base.AISLAMIENTO)
class ConcurrenciaAplicacionTests(DatosAplicacion, TransactionTestCase):
    paralelos = base.ConcurrenciaRecaudosTests.paralelos

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_confirmaciones_no_aplican_el_mismo_ingreso_dos_veces(self):
        reporte = self.ingreso()
        resultados, errores = self.paralelos([lambda: self.aplicar(reporte)] * 2)
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0].pk, resultados[1].pk)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_ingresos_no_pagan_la_misma_cuota(self):
        primero, segundo = self.ingreso(), self.ingreso()
        resultados, errores = self.paralelos([
            lambda: self.aplicar(primero), lambda: self.aplicar(segundo),
        ])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
