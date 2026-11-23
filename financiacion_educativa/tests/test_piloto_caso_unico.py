from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from financiacion_educativa.models_gobernanza_libro import ControlPilotoLibro, EventoControlPilotoLibro
from financiacion_educativa.models_libro import LineaLibroEducativo, MovimientoLibroEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services import gobernanza_libro as gobierno, libro_pagos as libro
from financiacion_educativa.tests.piloto_libro_helpers import politica_sintetica
from financiacion_educativa.tests.test_libro_pagos import DatosLibro
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class PilotoCasoUnicoTests(DatosLibro, TestCase):
    def setUp(self):
        super().setUp()
        self.politica = politica_sintetica(
            self.institucion, self.actor, self.aprobador, 'CUOTA_CASO_UNICO',
            self.politica.vigente_desde, reemplaza_id=self.politica.pk,
            reglas={'modalidad': libro.MODALIDAD_CUOTA_CONTRACTUAL, 'max_cuotas_vencidas': 1},
        )
        self.designado = self.nuevo_ingreso(self.cuota.valor_cuota)
        self.otro = self.nuevo_ingreso(self.cuota.valor_cuota)

    def control(self, activo, **kwargs):
        return gobierno.controlar_piloto(institucion_id=self.institucion.pk, activo=activo,
            actor=self.aprobador, motivo='Control sintético', **kwargs)

    def activar(self):
        self.control(False)
        return self.control(True, caso_unico=True, ingreso_designado_id=self.designado.pk)

    def confirmar(self, ingreso, token):
        return dict(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(),
            motivo='Cuota contractual de ensayo', confirmar=True)

    def test_formulario_exige_designacion_y_audita_cierre(self):
        previo = self.control(False)
        url = reverse('financiacion_educativa_web:operaciones:control-piloto-libro')
        self.client.force_login(self.aprobador)
        datos = {'institucion': self.institucion.pk, 'activo': 'ON', 'modalidad': 'CASO_UNICO',
                 'motivo': 'Ensayo sintético', 'confirmar': 'on'}
        ambiguo = self.client.post(url, {**datos, 'modalidad': ''})
        self.assertContains(ambiguo, 'Selecciona la modalidad')
        respuesta = self.client.post(url, datos)
        self.assertContains(respuesta, 'Designa una conciliación')
        self.assertEqual(ControlPilotoLibro.objects.get().revision, previo.revision)
        respuesta = self.client.post(url, {**datos, 'ingreso_designado': uuid4()})
        self.assertContains(respuesta, 'no pertenece a la institución o no existe')
        respuesta = self.client.post(url, {**datos, 'ingreso_designado': self.designado.pk})
        self.assertEqual(respuesta.status_code, 302)
        abierto = ControlPilotoLibro.objects.get()
        self.assertTrue(abierto.activo and abierto.caso_unico)
        self.assertEqual(abierto.ingreso_designado_id, self.designado.pk)
        with self.assertRaisesMessage(ValidationError, 'Detén el piloto'):
            self.control(True, caso_unico=True, ingreso_designado_id=self.otro.pk)
        respuesta = self.client.post(url, {**datos, 'activo': 'OFF', 'modalidad': ''})
        self.assertEqual(respuesta.status_code, 302)
        cerrado = ControlPilotoLibro.objects.get()
        self.assertFalse(cerrado.activo)
        with self.assertRaisesMessage(ValidationError, 'Designa una conciliación'):
            self.control(True)
        self.assertEqual(list(EventoControlPilotoLibro.objects.filter(
            revision__in=[abierto.revision, cerrado.revision]).order_by('revision')
            .values_list('activo', 'caso_unico', 'ingreso_designado_id')), [
                (True, True, self.designado.pk), (False, True, self.designado.pk),
            ])

    def test_contabilizacion_reintento_compensacion_y_cierre(self):
        _, token_otro = self.preview(self.otro, 'FULL')
        self.activar()
        with self.assertRaisesMessage(ValidationError, 'no es la designada'):
            self.preview(self.otro, 'FULL')
        with self.assertRaises(ValidationError) as error:
            libro.contabilizar(**self.confirmar(self.otro, token_otro))
        self.assertEqual(error.exception.code, 'PILOT_OTHER_INCOME')
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        _, token = self.preview(self.designado, 'FULL')
        datos = self.confirmar(self.designado, token)
        asiento = libro.contabilizar(**datos)
        self.assertEqual(libro.contabilizar(**datos).pk, asiento.pk)
        self.assertEqual((asiento.capital, asiento.intereses), (self.cuota.capital, self.cuota.interes))
        self.assertEqual(LineaLibroEducativo.objects.count(), 2)
        compensar = self.datos_compensacion(asiento)
        compensacion = libro.compensar(**compensar)
        self.assertEqual(libro.compensar(**compensar).pk, compensacion.pk)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 2)
        balance = libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.all())
        self.assertEqual(balance, {'recibido': 2 * self.cuota.valor_cuota,
            'capital': 0, 'intereses': 0, 'sin_destino': 2 * self.cuota.valor_cuota})
        self.control(False)
        for accion, argumentos in ((libro.contabilizar, datos), (libro.compensar, compensar)):
            with self.subTest(accion=accion.__name__), self.assertRaises(ValidationError) as error:
                accion(**argumentos)
            self.assertEqual(error.exception.code, 'PILOT_CLOSED')
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 2)

    def test_compensar_movimiento_del_otro_ingreso_bloquea_token_anterior(self):
        asiento = self.registrar(self.otro, 'FULL')
        compensar = self.datos_compensacion(asiento)
        self.activar()
        with self.assertRaisesMessage(ValidationError, 'no es la designada'):
            libro.preparar_compensacion(movimiento_id=asiento.pk, actor=self.aprobador)
        with self.assertRaises(ValidationError) as error:
            libro.compensar(**compensar)
        self.assertEqual(error.exception.code, 'PILOT_OTHER_INCOME')
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 1)
        self.assertFalse(MovimientoLibroEducativo.objects.filter(compensa=asiento).exists())
