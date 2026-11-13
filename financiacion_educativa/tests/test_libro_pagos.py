from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from financiacion_educativa.models import ArtefactoContractualEducativo
from financiacion_educativa.models_libro import PoliticaLibroEducativo, MovimientoLibroEducativo, LineaLibroEducativo
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.services import libro_pagos as libro
from financiacion_educativa.services import recaudos, aplicacion_cuotas
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.dashboards.operaciones.reporte_recaudos import construir_tablero, filtrar_segmento
from financiacion_educativa.tests.test_aplicacion_cuotas import DatosAplicacion
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests import test_recaudos_breb as base_recaudos


class DatosLibro(DatosAplicacion):
    def setUp(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 7, 1)):
            super().setUp()
        self.fecha = date(2026, 7, 16)
        self.politica = PoliticaLibroEducativo.objects.create(
            institucion=self.institucion, version='SYNTHETIC_TEST_ONLY_V1', reglas=dict(libro.REGLAS_SINTETICAS),
            referencia_aprobacion='SYNTHETIC-NOT-A-BUSINESS-APPROVAL', aprobada_por=self.actor,
            vigente_desde=date(2026, 7, 1))

    def nuevo_ingreso(self, monto='100000.00', fecha=None):
        fecha = fecha or self.fecha
        reporte, _ = self.reportar(monto=monto, fecha=fecha)
        self.escanear(reporte.comprobante_id)
        ingreso, _ = recaudos.confirmar_recaudo(**self.datos_confirmacion(
            reporte, fecha=fecha, referencia=uuid4().hex))
        return ingreso

    def preview(self, ingreso, modo='PARTIAL', **kwargs):
        return libro.preparar_preview(ingreso_id=ingreso.pk, actor=self.actor, modo=modo,
            politica_id=kwargs.pop('politica_id', self.politica.pk), **kwargs)

    def registrar(self, ingreso, modo='PARTIAL', **kwargs):
        _, token = self.preview(ingreso, modo)
        return libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token,
            clave=kwargs.pop('clave', uuid4()), motivo='Distribucion sintetica verificada', confirmar=True, **kwargs)


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class LibroPagosTests(DatosLibro, TestCase):
    def test_historico_completo_se_consulta_una_vez_sin_copiar(self):
        reporte = self.ingreso()
        app = self.aplicar(reporte)
        resumen = libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.all())
        self.assertEqual(resumen['capital'], app.capital_aplicado)
        self.assertEqual(resumen['intereses'], app.interes_aplicado)
        self.assertEqual(resumen['sin_destino'], 0)
        estado = libro.estado_libro(self.fotografia)
        self.assertEqual(estado['plan'][0]['numero'], 2)
        self.assertEqual(estado['capital'], str(self.fotografia.capital_financiado - app.capital_aplicado))
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_cuota_completa_nueva_capital_intereses_y_conservacion(self):
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota)
        anterior = (list(self.fotografia.cuotas.values()), type(self.fotografia).objects.values().get(pk=self.fotografia.pk),
                    list(ArtefactoContractualEducativo.objects.values()), obtener_resultado_publico(self.solicitud))
        with patch('requests.sessions.Session.request') as http:
            movimiento = self.registrar(ingreso, 'FULL')
        http.assert_not_called()
        self.assertEqual(movimiento.capital, self.cuota.capital)
        self.assertEqual(movimiento.intereses, self.cuota.interes)
        self.assertEqual(libro.estado_libro(self.fotografia)['plan'][0]['numero'], 2)
        self.assertEqual(libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.all())['sin_destino'], 0)
        self.assertEqual(anterior, (list(self.fotografia.cuotas.values()), type(self.fotografia).objects.values().get(pk=self.fotografia.pk),
                    list(ArtefactoContractualEducativo.objects.values()), obtener_resultado_publico(self.solicitud)))

    def test_parcial_varias_vencidas_orden_intereses_capital_mas_antigua(self):
        ingreso = self.nuevo_ingreso('100000', fecha=date(2026, 10, 5))
        m = self.registrar(ingreso)
        self.assertEqual(m.intereses, self.cuota.interes)
        self.assertEqual(m.capital, Decimal('100000') - self.cuota.interes)
        self.assertEqual(set(m.lineas.exclude(concepto='UNALLOCATED').values_list('cuota_id', flat=True)), {self.cuota.pk})
        estado = libro.estado_libro(self.fotografia)
        self.assertEqual(estado['plan'][0]['numero'], 1)
        self.assertEqual(Decimal(estado['plan'][0]['capital']), self.cuota.capital - m.capital)

    def test_alternativa_capital_primero_cambia_resultado_sin_regla_productiva(self):
        estado = libro.estado_libro(self.fotografia)
        reglas = {**libro.REGLAS_SINTETICAS, 'imputacion': 'CAPITAL_FIRST'}
        resultado = libro.calcular_distribucion(foto=self.fotografia, estado=estado,
            recibido='10000', fecha=self.fecha, modo='PARTIAL', reglas=reglas)
        self.assertEqual(Decimal(resultado['capital']), 10000)
        self.assertEqual(Decimal(resultado['intereses']), 0)
        self.assertEqual(resultado['despues']['plan'][0]['interes'], str(self.cuota.interes))

    def test_parcial_cruza_cuotas_vencidas_y_solo_cubre_la_primera(self):
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota + Decimal('100'), fecha=date(2026, 10, 5))
        self.registrar(ingreso)
        cuotas = libro.anotar_cuotas(self.fotografia.cuotas.all()).order_by('numero')
        self.assertTrue(cuotas[0].cubierta_libro)
        self.assertFalse(cuotas[1].cubierta_libro)
        self.assertEqual(cuotas[1].interes_cubierto, 100)
        self.assertEqual(libro.estado_libro(self.fotografia)['plan'][0]['numero'], 2)

    def test_interes_anticipado_no_se_pierde_al_reproyectar(self):
        self.registrar(self.nuevo_ingreso())
        with self.assertRaisesMessage(ValidationError, 'anticipados'):
            self.preview(self.nuevo_ingreso('400000'), 'PRINCIPAL')

    def test_principal_con_centavos_pendientes_falla_sin_perder_dinero(self):
        ingreso = self.nuevo_ingreso('400000.01')
        with self.assertRaisesMessage(ValidationError, 'centavos'):
            self.preview(ingreso, 'PRINCIPAL')
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_abono_con_vencidas_se_bloquea_sin_refinanciar_atraso(self):
        ingreso = self.nuevo_ingreso('400000', fecha=date(2026, 10, 5))
        with self.assertRaisesMessage(ValidationError, 'vencidas'):
            self.preview(ingreso, 'PRINCIPAL')

    def test_abono_conserva_cuota_reduce_plazo_y_ajusta_ultima(self):
        ingreso = self.nuevo_ingreso('400000')
        previo, _ = self.preview(ingreso, 'PRINCIPAL')
        m = self.registrar(ingreso, 'PRINCIPAL')
        nuevo = m.estado_despues['plan']
        self.assertLess(len(nuevo), self.fotografia.cuotas.count())
        self.assertEqual(Decimal(nuevo[0]['capital']) + Decimal(nuevo[0]['interes']), self.fotografia.valor_cuota_estimada)
        self.assertLessEqual(Decimal(nuevo[-1]['capital']) + Decimal(nuevo[-1]['interes']), self.fotografia.valor_cuota_estimada)
        self.assertGreater(m.intereses, 0)
        self.assertEqual(sum(Decimal(c['capital']) for c in nuevo), Decimal(previo['resultado']['despues']['capital']))
        futuros_nuevos = sum(Decimal(c['interes']) for c in nuevo)
        futuros_antes = sum(c.interes for c in self.fotografia.cuotas.all()) - m.intereses
        self.assertEqual(Decimal(previo['resultado']['intereses_futuros_excluidos']), futuros_antes - futuros_nuevos)

    def liquidacion(self):
        ingreso = self.nuevo_ingreso('2000000')
        previo, _ = self.preview(ingreso, 'PAYOFF')
        return Decimal(previo['resultado']['capital']) + Decimal(previo['resultado']['intereses'])

    def test_pago_total_separa_futuros_y_preview_no_cierra(self):
        total = self.liquidacion()
        ingreso = self.nuevo_ingreso(total)
        previo, _ = self.preview(ingreso, 'PAYOFF')
        self.assertGreater(Decimal(previo['resultado']['intereses_futuros_excluidos']), 0)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        self.assertTrue(libro.estado_libro(self.fotografia)['plan'])
        m = self.registrar(ingreso, 'PAYOFF')
        self.assertEqual(m.capital, self.fotografia.capital_financiado)
        self.assertEqual(m.estado_despues['plan'], [])
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertTrue(obtener_resultado_publico(self.solicitud).curso_autorizado)

    def test_excedente_identificado_y_cuadre_del_dinero(self):
        total = self.liquidacion()
        ingreso = self.nuevo_ingreso(total + Decimal('5500.01'))
        m = self.registrar(ingreso, 'PAYOFF')
        self.assertEqual(m.sin_destino, Decimal('5500.01'))
        self.assertEqual(m.importe, m.capital + m.intereses + m.sin_destino)
        resumen = libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk))
        self.assertEqual(resumen['recibido'], resumen['capital'] + resumen['intereses'] + resumen['sin_destino'])

    def test_falta_un_centavo_no_liquida_ni_redondea_banco(self):
        total = self.liquidacion()
        ingreso = self.nuevo_ingreso(total - Decimal('0.01'))
        with self.assertRaisesMessage(ValidationError, 'centavo'):
            self.preview(ingreso, 'PAYOFF')
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_fecha_retroactiva_falla_sin_reordenar_historico(self):
        self.registrar(self.nuevo_ingreso(fecha=date(2026, 7, 20)))
        ingreso = self.nuevo_ingreso(fecha=date(2026, 7, 19))
        with self.assertRaisesMessage(ValidationError, 'retroactiva'):
            self.preview(ingreso)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    def test_compensacion_resta_neto_restaura_estado_y_conserva_original(self):
        ingreso = self.nuevo_ingreso()
        m = self.registrar(ingreso)
        antes = MovimientoLibroEducativo.objects.values().get(pk=m.pk)
        datos = dict(movimiento_id=m.pk, actor=self.actor, clave=uuid4(), motivo='Error sintetico autorizado', secuencia_esperada=m.secuencia)
        compensacion = libro.compensar(**datos)
        self.assertEqual(libro.compensar(**datos).pk, compensacion.pk)
        self.assertEqual(MovimientoLibroEducativo.objects.values().get(pk=m.pk), antes)
        resumen = libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk))
        self.assertEqual(resumen['capital'], 0)
        self.assertEqual(resumen['intereses'], 0)
        self.assertEqual(resumen['sin_destino'], ingreso.monto_recibido)
        self.assertEqual(Decimal(libro.estado_libro(self.fotografia)['capital']), self.fotografia.capital_financiado)

    def test_compensacion_con_sucesores_o_permiso_insuficiente_bloqueada(self):
        m = self.registrar(self.nuevo_ingreso())
        self.registrar(self.nuevo_ingreso())
        with self.assertRaisesMessage(ValidationError, 'ultimo'):
            libro.compensar(movimiento_id=m.pk, actor=self.actor, clave=uuid4(), motivo='Motivo sintetico', secuencia_esperada=2)
        with self.assertRaises(PermissionDenied):
            libro.compensar(movimiento_id=m.pk, actor=self.pagador, clave=uuid4(), motivo='Motivo', secuencia_esperada=2)

    def test_replay_y_payload_distinto_no_duplican(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        datos = dict(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(), motivo='Verificado', confirmar=True)
        m = libro.contabilizar(**datos)
        self.assertEqual(libro.contabilizar(**datos).pk, m.pk)
        with self.assertRaises(ValidationError):
            libro.contabilizar(**{**datos, 'motivo': 'Otro'})
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    def test_preview_vencido_manipulado_o_estado_alterado_exige_revision(self):
        ingreso, otro = self.nuevo_ingreso(), self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        datos = dict(ingreso_id=ingreso.pk, actor=self.actor, clave=uuid4(), motivo='Verificado', confirmar=True)
        for invalido in ('tampered', token + 'x'):
            with self.assertRaises(ValidationError):
                libro.contabilizar(token=invalido, **datos)
        with patch('django.core.signing.time.time', return_value=9999999999), self.assertRaises(ValidationError):
            libro.contabilizar(token=token, **datos)
        self.registrar(otro)
        with self.assertRaisesMessage(ValidationError, 'cambio'):
            libro.contabilizar(token=token, **datos)

    def test_bloqueo_por_defecto_y_simulacion_no_es_politica_aprobada(self):
        ingreso = self.nuevo_ingreso()
        for politica_id, flag in ((None, True), (self.politica.pk, False)):
            _, token = self.preview(ingreso, politica_id=politica_id)
            with override_settings(FINANCIACION_EDUCATIVA_LIBRO_ENABLED=flag), self.assertRaisesMessage(ValidationError, 'bloqueada'):
                libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(), motivo='Verificado', confirmar=True)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())

    def test_politica_ajena_no_aplica(self):
        p = PoliticaLibroEducativo.objects.create(institucion=self.otra, version='SYNTHETIC', reglas=dict(libro.REGLAS_SINTETICAS),
            referencia_aprobacion='SYNTHETIC', aprobada_por=self.actor, vigente_desde=date(2026, 7, 1))
        with self.assertRaises(ValidationError):
            self.preview(self.nuevo_ingreso(), politica_id=p.pk)

    def test_dos_instituciones_y_ingreso_directo_operaciones_sin_reporte(self):
        from financiacion_educativa.dashboards.operaciones.reporte_recaudos import cohorte_bancaria
        primero = self.registrar(self.nuevo_ingreso())
        with patch('django.utils.timezone.localdate', return_value=date(2026, 7, 1)):
            solicitud, foto, _, _ = self.contrato('LIBRO-SEGUNDA-INSTITUCION', institucion=self.otra)
        politica = PoliticaLibroEducativo.objects.create(institucion=self.otra, version='SYNTHETIC_INST_2',
            reglas=dict(libro.REGLAS_SINTETICAS), referencia_aprobacion='SYNTHETIC_ONLY',
            aprobada_por=self.actor, vigente_desde=date(2026, 7, 1))
        ingreso, _ = recaudos.registrar_pago_recibido(solicitud_id=solicitud.pk, actor=self.actor,
            cuenta_id=self.receptor.cuenta_id, monto='50000', fecha=self.fecha,
            referencia=uuid4().hex, evidencia_id=self.evidencia().pk, clave=uuid4())
        self.assertEqual(ingreso.reporte.origen, 'OPERATIONS')
        self.assertIsNone(ingreso.reporte.comprobante_id)
        _, token = libro.preparar_preview(ingreso_id=ingreso.pk, actor=self.actor, modo='PARTIAL', politica_id=politica.pk)
        segundo = libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token,
            clave=uuid4(), motivo='Ingreso sintetico directo', confirmar=True)
        for institucion, esperado in ((self.institucion, primero), (self.otra, segundo)):
            datos = {'desde': self.fecha, 'hasta': self.fecha, 'institucion': institucion.pk, 'estado': 'CONFIRMED'}
            tablero = construir_tablero(cohorte_bancaria(datos), datos)
            self.assertEqual(tablero['totales']['capital'], esperado.capital)
            self.assertEqual(tablero['totales']['intereses'], esperado.intereses)
            self.assertEqual(tablero['totales']['cantidad_confirmados'], 1)

    def test_rollback_lineas_no_deja_cabecera_ni_correo(self):
        ingreso = self.nuevo_ingreso()
        from financiacion_educativa.models import OutboxCorreoEducativo
        cantidad = OutboxCorreoEducativo.objects.count()
        with patch.object(LineaLibroEducativo.objects, 'create', side_effect=ValidationError('synthetic failure')), self.assertRaises(ValidationError):
            self.registrar(ingreso)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        self.assertEqual(OutboxCorreoEducativo.objects.count(), cantidad)
        self.registrar(ingreso)

    def test_orm_inmutable_y_constraint_cuadre(self):
        m = self.registrar(self.nuevo_ingreso())
        with self.assertRaisesMessage(ValidationError, 'agregar lineas'):
            LineaLibroEducativo.objects.create(movimiento=m, cuota=self.cuota, concepto='CAPITAL', importe='1', referencia_periodo='SYNTHETIC')
        for llamada in (m.save, m.delete, lambda: MovimientoLibroEducativo.objects.update(capital=1),
                        lambda: MovimientoLibroEducativo._base_manager.all().delete(),
                        lambda: MovimientoLibroEducativo.objects.bulk_create([m])):
            with self.assertRaises(ValidationError):
                llamada()
        m.id = uuid4()
        m.clave = uuid4()
        m.secuencia = 2
        m.capital += 1
        with self.assertRaises(IntegrityError), transaction.atomic():
            m.save_base(raw=True, force_insert=True)

    def test_limpieza_qa_reconoce_libro_como_evidencia_preservada(self):
        from financiacion_educativa.services.limpieza_solicitudes import construir_plan_limpieza
        m = self.registrar(self.nuevo_ingreso())
        plan = construir_plan_limpieza(self.institucion)
        self.assertEqual(dict(plan.conteos)['MovimientoLibroEducativo'], 1)
        self.assertEqual(dict(plan.conteos)['LineaLibroEducativo'], m.lineas.count())
        self.assertTrue(any('MovimientoLibroEducativo' in r for r in plan.relaciones_protegidas))
        self.assertTrue(MovimientoLibroEducativo.objects.filter(pk=m.pk).exists())

    def test_tablero_seguimiento_y_libro_cuadran_sin_doble_conteo(self):
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota)
        m = self.registrar(ingreso, 'FULL')
        datos = {'desde': self.fecha, 'hasta': self.fecha, 'estado': 'CONFIRMED'}
        consulta = ConciliacionRecaudoEducativo.objects.all()
        tablero = construir_tablero(consulta, datos)
        self.assertEqual(tablero['totales']['capital'], m.capital)
        self.assertEqual(tablero['totales']['intereses'], m.intereses)
        self.assertEqual(tablero['totales']['pendientes'], 0)
        self.assertEqual(filtrar_segmento(consulta, 'APPLIED').count(), 1)
        self.client.force_login(self.actor)
        detalle = self.client.get(reverse('financiacion_educativa_web:operaciones:seguimiento-detalle', args=[self.solicitud.pk]))
        self.assertEqual(detalle.status_code, 200)
        self.assertEqual(detalle.context['resumen_libro']['capital'], m.capital)
        self.assertEqual(detalle.context['pendientes'][0].numero, 2)
        self.assertEqual(detalle.context['fotografia'].ingresos_abiertos, 0)

    def test_proyeccion_sustituye_cobro_original_y_total_no_muestra_otra_cuota(self):
        from financiacion_educativa.web.portal_estudiante import calendario_del_portal
        m = self.registrar(self.nuevo_ingreso('400000'), 'PRINCIPAL')
        portal = calendario_del_portal(self.fotografia)
        self.assertTrue(portal['plan_libro_modificado'])
        fila = m.estado_despues['plan'][0]
        self.assertEqual(portal['primera_cuota_pendiente']['valor_cuota'], Decimal(fila['capital']) + Decimal(fila['interes']))
        self.client.force_login(self.actor)
        seguimiento = self.client.get(reverse('financiacion_educativa_web:operaciones:seguimiento-cartera'))
        self.assertEqual(seguimiento.context['totales']['cuotas'], 0)
        self.assertEqual(seguimiento.context['proyecciones'][0]['referencia'], self.solicitud.referencia_externa)
        self.registrar(self.nuevo_ingreso('2000000'), 'PAYOFF')
        self.assertIsNone(calendario_del_portal(self.fotografia)['primera_cuota_pendiente'])
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')

    def test_vistas_solo_lectura_permisos_y_confirmacion_bloqueada(self):
        ingreso = self.nuevo_ingreso()
        url = reverse('financiacion_educativa_web:operaciones:libro-preview', args=[ingreso.pk])
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.actor)
        r = self.client.get(url)
        self.assertContains(r, 'Contabilizacion bloqueada')
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        consulta = self.client.get(reverse('financiacion_educativa_web:operaciones:libro-ingreso', args=[ingreso.pk]))
        self.assertContains(consulta, 'Dinero sin destino')

    def test_http_preview_confirmacion_y_replay_con_politica_sintetica(self):
        ingreso = self.nuevo_ingreso()
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:libro-preview', args=[ingreso.pk])
        previo = self.client.get(url, {'modo': 'PARTIAL', 'politica': self.politica.pk})
        self.assertContains(previo, 'Contabilizar con politica aprobada')
        datos = {**previo.context['confirmacion'].initial, 'motivo': 'Intencion verificada sintetica', 'confirmar': 'on'}
        destino = reverse('financiacion_educativa_web:operaciones:libro-ingreso', args=[ingreso.pk])
        self.assertRedirects(self.client.post(url, datos), destino)
        self.assertRedirects(self.client.post(url, datos), destino)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    def test_consultar_y_conciliar_no_otorgan_contabilizar_o_compensar(self):
        ingreso = self.nuevo_ingreso()
        operador = get_user_model().objects.create_user('consulta-libro', 'consulta@example.test', 'synthetic-only')
        codigos = [p.rsplit('.', 1)[-1] for p in (aplicacion_cuotas.PERMISO_ACCESO, recaudos.PERMISO_CONSULTAR, recaudos.PERMISO_CONCILIAR)]
        operador.user_permissions.add(*Permission.objects.filter(content_type__app_label='financiacion_educativa', codename__in=codigos))
        self.client.force_login(operador)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:libro-ingreso', args=[ingreso.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:libro-preview', args=[ingreso.pk])).status_code, 403)
        operador.user_permissions.add(Permission.objects.get(content_type__app_label='financiacion_educativa', codename='aplicar_cuotas_educativas'))
        operador = get_user_model().objects.get(pk=operador.pk)
        m = self.registrar(ingreso)
        with self.assertRaises(PermissionDenied):
            libro.compensar(movimiento_id=m.pk, actor=operador, clave=uuid4(), motivo='Motivo', secuencia_esperada=1)

    def test_nuevo_escaneo_o_firma_incoherente_bloquean_al_confirmar(self):
        from financiacion_educativa.models_recaudos import IntentoEscaneoRecaudo
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        IntentoEscaneoRecaudo.objects.create(archivo=ingreso.evidencia_movimiento, numero=2,
            sha256=ingreso.evidencia_movimiento.sha256)
        with self.assertRaises(ValidationError):
            libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(), motivo='Verificado', confirmar=True)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        otro = self.nuevo_ingreso()
        _, otro_token = self.preview(otro)
        from financiacion_educativa.models import ProcesoFirmaEducativa
        ProcesoFirmaEducativa.objects.filter(solicitud=self.solicitud).update(estado='SENT')
        with self.assertRaises(ValidationError):
            libro.contabilizar(ingreso_id=otro.pk, actor=self.actor, token=otro_token, clave=uuid4(), motivo='Verificado', confirmar=True)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class ConcurrenciaLibroTests(DatosLibro, TransactionTestCase):
    paralelos = base_recaudos.ConcurrenciaRecaudosTests.paralelos

    @skipUnlessDBFeature('has_select_for_update')
    def test_mismo_ingreso_y_clave_dos_reintentos_no_duplican(self):
        ingreso = self.nuevo_ingreso()
        _, token = self.preview(ingreso)
        datos = dict(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(), motivo='Verificado', confirmar=True)
        resultados, errores = self.paralelos([lambda: libro.contabilizar(**datos)] * 2)
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0].pk, resultados[1].pk)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)
        self.assertTrue(connection.is_usable())

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_operadores_distintos_sobre_mismo_ingreso(self):
        ingreso = self.nuevo_ingreso()
        otro = get_user_model().objects.create_superuser('otro-libro', 'otro@example.test', 'synthetic-only')
        _, a = self.preview(ingreso)
        _, b = libro.preparar_preview(ingreso_id=ingreso.pk, actor=otro, modo='PARTIAL', politica_id=self.politica.pk)
        resultados, errores = self.paralelos([
            lambda: libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=a, clave=uuid4(), motivo='Operador A', confirmar=True),
            lambda: libro.contabilizar(ingreso_id=ingreso.pk, actor=otro, token=b, clave=uuid4(), motivo='Operador B', confirmar=True)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_libro_y_aplicacion_integra_comparten_lock_solicitud(self):
        nuevo, antiguo = self.nuevo_ingreso(self.cuota.valor_cuota), self.nuevo_ingreso(self.cuota.valor_cuota)
        _, token = self.preview(nuevo, 'FULL')
        self.vistas[antiguo.reporte_id] = signing.dumps(
            aplicacion_cuotas._snapshot(antiguo.reporte, self.cuota, self.actor), salt=aplicacion_cuotas.VISTA_PREVIA_SALT)
        resultados, errores = self.paralelos([
            lambda: libro.contabilizar(ingreso_id=nuevo.pk, actor=self.actor, token=token, clave=uuid4(), motivo='Libro', confirmar=True),
            lambda: self.aplicar(antiguo.reporte)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(MovimientoLibroEducativo.objects.count() + AplicacionCuotaEducativa.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_ingresos_misma_obligacion_exigen_actualizar_preview(self):
        a, b = self.nuevo_ingreso(), self.nuevo_ingreso()
        _, ta = self.preview(a)
        _, tb = self.preview(b)
        resultados, errores = self.paralelos([
            lambda: libro.contabilizar(ingreso_id=a.pk, actor=self.actor, token=ta, clave=uuid4(), motivo='A', confirmar=True),
            lambda: libro.contabilizar(ingreso_id=b.pk, actor=self.actor, token=tb, clave=uuid4(), motivo='B', confirmar=True)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_compensaciones_no_duplican(self):
        m = self.registrar(self.nuevo_ingreso())
        datos = dict(movimiento_id=m.pk, actor=self.actor, clave=uuid4(), motivo='Correccion sintetica', secuencia_esperada=1)
        resultados, errores = self.paralelos([lambda: libro.compensar(**datos)] * 2)
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 2)
