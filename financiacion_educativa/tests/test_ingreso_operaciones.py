from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models_recaudos import (
    ArchivoRecaudoEducativo, AplicacionCuotaEducativa, ConciliacionRecaudoEducativo,
    ReporteRecaudoEducativo, CuentaRecaudoEducativa,
)
from financiacion_educativa.services import recaudos, aplicacion_cuotas
from financiacion_educativa.tests import test_recaudos_breb as base
from financiacion_educativa.tests.factories import imagen_jpeg_prueba


class DatosIngresoOperaciones(base.DatosRecaudosMixin):
    def setUp(self):
        self.preparar_recaudos()
        self.cuota = self.fotografia.cuotas.order_by('numero').first()
        self.banco = self.evidencia()
        self.datos = dict(solicitud_id=self.solicitud.pk, actor=self.actor,
            cuenta_id=self.receptor.cuenta_id, monto=self.cuota.valor_cuota,
            fecha=timezone.localdate(), referencia='OPS-BANK-001', evidencia_id=self.banco.pk, clave=uuid4())

    def registrar(self, **cambios):
        return recaudos.registrar_pago_recibido(**{**self.datos, **cambios})

    def operador(self, permisos):
        user = get_user_model().objects.create_user('ops-' + uuid4().hex, 'ops@example.test', 'test-only')
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa',
            codename__in=[p.rsplit('.', 1)[1] for p in permisos]))
        return user

    def url_registro(self):
        return reverse('financiacion_educativa_web:operaciones:registrar-pago-recibido', args=[self.solicitud.pk])

    def post(self, **cambios):
        return {**{k: v for k, v in self.datos.items() if k not in ('solicitud_id', 'actor')},
            'proposito': 'UNSPECIFIED', **cambios}


@override_settings(**base.AISLAMIENTO)
class IngresoOperacionesTests(DatosIngresoOperaciones, TestCase):
    def test_recorrido_sin_reporte_repite_registro_y_aplicacion_sin_filtrar_evidencia(self):
        from financiacion_educativa.services.estado_publico import obtener_resultado_publico

        self.assertFalse(ReporteRecaudoEducativo.objects.exists())
        cuotas_originales = list(self.fotografia.cuotas.values())
        contrato_original = obtener_resultado_publico(self.solicitud)
        self.client.force_login(self.actor)
        detalle_obligacion = self.detalle_url(self.solicitud)
        self.assertContains(self.client.get(detalle_obligacion), 'Registrar pago recibido')
        self.assertEqual(self.client.post(self.url_registro(), {
            'accion': 'evidencia', 'archivo': imagen_jpeg_prueba(marca='e2e-bank-only'),
        }).status_code, 200)
        banco = ArchivoRecaudoEducativo.objects.filter(finalidad='BANK_EVIDENCE').exclude(pk=self.banco.pk).get()
        self.assertEqual(banco.creado_por_id, self.actor.pk)
        self.assertEqual(banco.estado_escaneo, 'PENDING')
        datos = self.post(evidencia_id=banco.pk)
        pendiente = self.client.post(self.url_registro(), datos)
        self.assertEqual(pendiente.status_code, 200)
        self.assertTrue(pendiente.context['form'].errors)
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())
        self.escanear(banco.pk)
        banco.refresh_from_db()
        self.assertEqual(banco.estado_escaneo, 'SAFE')

        respuesta = self.client.post(self.url_registro(), datos)
        ingreso = ConciliacionRecaudoEducativo.objects.get()
        registro = ingreso.reporte
        detalle_recaudo = reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[registro.pk])
        self.assertRedirects(respuesta, detalle_recaudo)
        self.assertRedirects(self.client.post(self.url_registro(), datos), detalle_recaudo)
        # A fresh form/key still resolves the same normalized bank movement.
        repetido = {**datos, 'clave': uuid4(), 'referencia': ' ops-bank-001 '}
        self.assertRedirects(self.client.post(self.url_registro(), repetido), detalle_recaudo)
        self.assertEqual(ingreso.evidencia_movimiento_id, banco.pk)
        self.assertEqual(registro.origen, 'OPERATIONS')
        self.assertEqual(registro.reportado_por_id, self.actor.pk)
        self.assertIsNone(registro.comprobante_id)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())
        conciliacion_original = ConciliacionRecaudoEducativo.objects.values().get(pk=ingreso.pk)

        self.client.force_login(self.pagador)
        estado = reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, registro.pk])
        antes = self.client.get(estado)
        self.assertContains(antes, 'pendiente')
        self.assertNotContains(antes, 'Pago aplicado a la cuota')
        self.client.force_login(self.actor)
        form = self.client.get(detalle_recaudo).context['aplicar_form']
        aplicar = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[registro.pk])
        confirmacion = {**form.initial, 'confirmar_cuota': 'on'}
        self.assertRedirects(self.client.post(aplicar, confirmacion), detalle_recaudo)
        aplicacion = AplicacionCuotaEducativa.objects.get()
        self.assertRedirects(self.client.post(aplicar, confirmacion), detalle_recaudo)
        self.assertRedirects(self.client.post(self.url_registro(), repetido), detalle_recaudo)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
        self.assertEqual(AplicacionCuotaEducativa.objects.get().pk, aplicacion.pk)
        self.assertEqual(aplicacion.cuota_id, self.cuota.pk)
        self.assertEqual(aplicacion.conciliacion_id, ingreso.pk)
        self.assertEqual(aplicacion.actor_id, self.actor.pk)
        self.assertEqual(aplicacion.capital_aplicado + aplicacion.interes_aplicado, ingreso.monto_recibido)
        self.assertEqual(aplicacion.fecha_efectiva, ingreso.fecha_efectiva)
        self.assertEqual(list(self.fotografia.cuotas.values()), cuotas_originales)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.values().get(pk=ingreso.pk), conciliacion_original)
        self.solicitud.refresh_from_db()
        self.assertEqual(obtener_resultado_publico(self.solicitud), contrato_original)
        self.assertContains(self.client.get(detalle_obligacion), 'Pagada')

        self.client.force_login(self.pagador)
        pagina = self.client.get(estado)
        self.assertContains(pagina, f'Pago aplicado a la cuota {self.cuota.numero}')
        self.assertContains(pagina, 'Ingreso registrado por Operaciones')
        soporte = reverse('financiacion_educativa_web:operaciones:recaudo-soporte', args=[registro.pk])
        for privado in (soporte, str(banco.pk), banco.archivo.name, ingreso.referencia_verificada, self.actor.username):
            self.assertNotContains(pagina, privado)
        self.assertEqual(self.client.get(soporte + '?banco=1').status_code, 403)
        self.assertEqual(self.client.get(soporte + '?banco=1&download=1').status_code, 403)
        comprobante = reverse('financiacion_educativa_web:pago-comprobante', args=[self.solicitud.pk, registro.pk])
        self.assertEqual(self.client.get(comprobante + '?banco=1').status_code, 404)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(estado).status_code, 404)

    def test_registro_sin_reporte_previo_y_aplicacion_completa(self):
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 0)
        archivos = ArchivoRecaudoEducativo.objects.count()
        self.client.force_login(self.actor)
        detalle = reverse('financiacion_educativa_web:operaciones:obligacion-detalle', args=[self.solicitud.pk])
        self.assertContains(self.client.get(detalle), 'Registrar pago recibido')
        respuesta = self.client.post(self.url_registro(), self.post())
        ingreso = ConciliacionRecaudoEducativo.objects.get()
        self.assertRedirects(respuesta, reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[ingreso.reporte_id]))
        registro = ingreso.reporte
        self.assertEqual(registro.origen, 'OPERATIONS')
        self.assertEqual(registro.reportado_por, self.actor)
        self.assertEqual(ingreso.actor, self.actor)
        self.assertIsNone(registro.comprobante_id)
        self.assertIsNone(registro.receptor_id)
        self.assertEqual(registro.proposito, 'UNSPECIFIED')
        self.assertEqual(ArchivoRecaudoEducativo.objects.count(), archivos)
        self.assertEqual(ArchivoRecaudoEducativo.objects.filter(finalidad='RECEIPT').count(), 0)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())
        operativo = self.client.get(respuesta.url)
        self.assertContains(operativo, 'Registrado por Operaciones')
        self.assertContains(operativo, self.actor.username)
        form = operativo.context['aplicar_form']
        cuota_original = list(self.fotografia.cuotas.values())
        aplicar = reverse('financiacion_educativa_web:operaciones:recaudo-aplicar-cuota', args=[registro.pk])
        self.assertEqual(self.client.post(aplicar, {**form.initial, 'confirmar_cuota': 'on'}).status_code, 302)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
        self.assertEqual(list(self.fotografia.cuotas.values()), cuota_original)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.client.force_login(self.pagador)
        estado = reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, registro.pk])
        pagina = self.client.get(estado)
        self.assertContains(pagina, 'Pago aplicado a la cuota')
        self.assertContains(pagina, 'Ingreso registrado por Operaciones')
        self.assertNotContains(pagina, self.actor.username)
        comprobante = reverse('financiacion_educativa_web:pago-comprobante', args=[self.solicitud.pk, registro.pk])
        self.assertEqual(self.client.get(comprobante).status_code, 404)

    def test_registrar_no_concede_aplicar_ni_conciliar(self):
        user = self.operador((recaudos.PERMISO_CONSULTAR, recaudos.PERMISO_REGISTRAR, aplicacion_cuotas.PERMISO_ACCESO))
        banco = recaudos.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=user)
        self.escanear(banco.pk)
        ingreso, _ = self.registrar(actor=user, evidencia_id=banco.pk)
        self.assertFalse(user.has_perm(recaudos.PERMISO_CONCILIAR))
        self.assertFalse(user.has_perm(aplicacion_cuotas.PERMISO_APLICAR))
        with self.assertRaises(PermissionDenied):
            aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=user)
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[ingreso.reporte_id])), 'Confirmar aplicación')

    def test_conciliar_aplicar_staff_o_pagador_no_conceden_registrar(self):
        for permisos in (
            (recaudos.PERMISO_CONCILIAR, recaudos.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_ACCESO),
            (aplicacion_cuotas.PERMISO_APLICAR, recaudos.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_ACCESO),
            (recaudos.PERMISO_REGISTRAR,), (),
        ):
            user = self.operador(permisos)
            user.is_staff = True
            user.save()
            with self.subTest(permisos=permisos), self.assertRaises(PermissionDenied):
                self.registrar(actor=user)
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url_registro(), self.post()).status_code, 403)
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(self.url_registro()).status_code, 403)

    def test_evidencia_pendiente_ajena_o_alterada_bloquea_sin_registros_parciales(self):
        pendiente = recaudos.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=self.actor)
        otro = self.operador((recaudos.PERMISO_REGISTRAR,))
        ajena = recaudos.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=otro)
        self.escanear(ajena.pk)
        for evidencia in (pendiente, ajena, self.receptor.qr):
            with self.subTest(evidencia=evidencia.pk), self.assertRaises(ValidationError):
                self.registrar(evidencia_id=evidencia.pk)
        with self.banco.archivo.open('wb') as archivo:
            archivo.write(b'altered-synthetic-evidence')
        with self.assertRaises(ValidationError):
            self.registrar()
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 0)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 0)

    def test_cuenta_sin_qr_ni_receptor_activo_admite_ingreso_real(self):
        cuenta = CuentaRecaudoEducativa.objects.create(entidad='BANCOOPS', identificador_cuenta='TESTONLY', creada_por=self.actor)
        ingreso, _ = self.registrar(cuenta_id=cuenta.pk)
        self.assertEqual(ingreso.cuenta, cuenta)
        self.assertIsNone(ingreso.reporte.receptor_id)

    def test_diferencia_y_finalidad_no_aplicable_conservan_ingreso_pendiente(self):
        for datos in ({'monto': self.cuota.valor_cuota - Decimal('1')}, {'proposito': 'PRINCIPAL'}):
            ingreso, _ = self.registrar(**{**datos, 'clave': uuid4(), 'referencia': uuid4().hex})
            with self.assertRaises(ValidationError):
                aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 2)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())

    def test_idempotencia_clave_y_movimiento_normalizado(self):
        primero, replay = self.registrar()
        self.assertFalse(replay)
        repetido, replay = self.registrar()
        self.assertTrue(replay)
        self.assertEqual(primero.pk, repetido.pk)
        con_otra_clave, replay = self.registrar(clave=uuid4(), referencia=' ops-bank-001 ')
        self.assertTrue(replay)
        self.assertEqual(primero.pk, con_otra_clave.pk)
        with self.assertRaises(ValidationError):
            self.registrar(monto=self.cuota.valor_cuota + 1)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)

    def test_si_ya_existe_reporte_conciliado_no_crea_registro_operaciones(self):
        reporte, _ = self.reportar(monto=self.cuota.valor_cuota)
        self.escanear(reporte.comprobante_id)
        original, _ = recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte, self.banco, referencia='OPS-BANK-001'))
        ingreso, replay = self.registrar()
        self.assertTrue(replay)
        self.assertEqual(original.pk, ingreso.pk)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        self.assertEqual(ingreso.reporte.origen, 'PAYER')

    def test_reporte_posterior_redirige_al_ingreso_ops_sin_conciliar_otro(self):
        ingreso, _ = self.registrar()
        reporte, _ = self.reportar(monto=self.cuota.valor_cuota)
        self.escanear(reporte.comprobante_id)
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:recaudo-confirmar', args=[reporte.pk])
        datos = self.datos_confirmacion(reporte, self.banco, referencia='OPS-BANK-001')
        datos.pop('actor'); datos.pop('reporte_id'); datos.pop('cuenta_id')
        respuesta = self.client.post(url, datos)
        self.assertRedirects(respuesta, reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[ingreso.reporte_id]))
        self.assertFalse(ConciliacionRecaudoEducativo.objects.filter(reporte=reporte).exists())
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_membresia_otra_obligacion_y_cuenta_invalida_no_se_sustituyen(self):
        self.client.force_login(self.actor)
        respuesta = self.client.post(self.url_registro(), self.post(cuenta_id=uuid4()))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('cuenta_id', respuesta.context['form'].errors)
        self.assertEqual(str(respuesta.context['form']['clave'].value()), str(self.datos['clave']))
        self.solicitud.estado = 'PENDING_SIGNATURE'
        self.solicitud.save(update_fields=['estado'])
        with self.assertRaises(ValidationError):
            self.registrar()
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_formulario_no_acepta_atribuir_ingreso_al_estudiante_y_exige_csrf(self):
        self.client.force_login(self.actor)
        self.assertContains(self.client.get(self.url_registro()), 'Ingreso bancario sin reporte del estudiante')
        respuesta = self.client.post(self.url_registro(), self.post(actor=self.pagador.pk,
            reportado_por=self.pagador.pk, origen='PAYER', comprobante=self.receptor.qr_id))
        self.assertEqual(respuesta.status_code, 302)
        registro = ReporteRecaudoEducativo.objects.get()
        self.assertEqual(registro.reportado_por, self.actor)
        self.assertEqual(registro.origen, 'OPERATIONS')
        self.assertIsNone(registro.comprobante_id)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.actor)
        self.assertEqual(cliente.post(self.url_registro(), self.post()).status_code, 403)

    def test_datos_obligatorios_montos_fechas_y_referencias(self):
        self.client.force_login(self.actor)
        vacio = self.client.post(self.url_registro(), {})
        self.assertEqual(vacio.status_code, 200)
        self.assertTrue({'cuenta_id', 'monto', 'fecha', 'referencia', 'evidencia_id'}.issubset(vacio.context['form'].errors))
        for cambio in ({'monto': 0}, {'fecha': timezone.localdate() + timedelta(days=1)},
                       {'referencia': None}, {'referencia': 'INVALID REFERENCE'}, {'cuenta_id': uuid4()}):
            with self.subTest(cambio=cambio), self.assertRaises(ValidationError):
                self.registrar(**cambio)
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())

    def test_reporte_pagador_conserva_origen_y_comprobante_obligatorio(self):
        reporte, _ = self.reportar()
        self.assertEqual(reporte.origen, 'PAYER')
        self.assertIsNotNone(reporte.comprobante_id)
        with self.assertRaises(ValidationError):
            self.reportar(proposito='UNSPECIFIED')
        datos = dict(solicitud=self.solicitud, fotografia=self.fotografia, reportado_por=self.pagador,
            clave_idempotencia=uuid4(), payload_sha256='a' * 64, proposito='INSTALLMENT',
            monto_reportado=100, fecha_declarada=timezone.localdate())
        with self.assertRaises(ValidationError):
            ReporteRecaudoEducativo.objects.create(**datos)
        with self.assertRaises(ValidationError):
            ReporteRecaudoEducativo.objects.create(**{**datos, 'origen': 'OPERATIONS', 'comprobante': reporte.comprobante})

    def test_upload_bancario_sin_conciliar_ni_crear_reporte(self):
        user = self.operador((recaudos.PERMISO_CONSULTAR, recaudos.PERMISO_REGISTRAR, aplicacion_cuotas.PERMISO_ACCESO))
        self.client.force_login(user)
        respuesta = self.client.post(self.url_registro(), {'accion': 'evidencia', 'archivo': imagen_jpeg_prueba()})
        self.assertEqual(respuesta.status_code, 200)
        banco = ArchivoRecaudoEducativo.objects.get(creado_por=user)
        self.assertEqual(banco.finalidad, 'BANK_EVIDENCE')
        self.assertEqual(banco.estado_escaneo, 'PENDING')
        self.assertEqual(recaudos.evidencias_bancarias_elegibles(user, permiso=recaudos.PERMISO_REGISTRAR).count(), 0)
        self.escanear(banco.pk)
        self.assertEqual(recaudos.evidencias_bancarias_elegibles(user, permiso=recaudos.PERMISO_REGISTRAR).count(), 1)
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())


@override_settings(**base.AISLAMIENTO)
class ConcurrenciaIngresoOperacionesTests(DatosIngresoOperaciones, TransactionTestCase):
    paralelos = base.ConcurrenciaRecaudosTests.paralelos

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_registros_operativos_un_solo_ingreso(self):
        resultados, errores = self.paralelos([lambda: self.registrar(), lambda: self.registrar(clave=uuid4())])
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0][0].pk, resultados[1][0].pk)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_ambos_flujos_compiten_por_el_mismo_movimiento(self):
        from financiacion_educativa.models import OutboxCorreoEducativo

        reporte, _ = self.reportar(monto=self.cuota.valor_cuota)
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte, self.banco, referencia='OPS-BANK-001')
        resultados, errores = self.paralelos([lambda: self.registrar(), lambda: recaudos.confirmar_recaudo(**datos)])
        self.assertTrue(resultados)
        self.assertTrue(all(isinstance(e, recaudos.MovimientoBancarioRegistrado) for e in errores), repr(errores))
        self.assertEqual(len(resultados) + len(errores), 2)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
        ganador = ConciliacionRecaudoEducativo.objects.get()
        self.assertEqual(ReporteRecaudoEducativo.objects.filter(origen='OPERATIONS').count(), int(ganador.reporte.origen == 'OPERATIONS'))
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_INCOME_CONFIRMED').count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_movimiento_global_entre_instituciones_no_se_duplica(self):
        otra, _, _, _ = self.contrato('OPS-OTHER-TENANT', self.otra)
        resultados, errores = self.paralelos([lambda: self.registrar(),
            lambda: self.registrar(solicitud_id=otra.pk, clave=uuid4())])
        self.assertEqual(errores, [])
        self.assertEqual(resultados[0][0].pk, resultados[1][0].pk)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)
