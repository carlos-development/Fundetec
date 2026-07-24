from decimal import Decimal

from django.test import TestCase

from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.idempotencia import (
    crear_solicitud_idempotente,
)
from financiacion_educativa.services.solicitudes import DatosSolicitudFinanciacion
from instituciones.models import Institucion


class ServicioIdempotenciaTests(TestCase):
    def test_servicio_funciona_sin_depender_de_vistas(self):
        institucion = Institucion.objects.create(
            nombre_comercial='Institucion dominio',
            razon_social='Institucion dominio SAS',
            numero_identificacion_tributaria='901777777',
        )
        datos = DatosSolicitudFinanciacion(
            referencia_externa='DOMINIO-001',
            nombres='ANA',
            apellidos='PEREZ',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=6,
            nombre_curso='Programacion',
        )

        primera = crear_solicitud_idempotente(
            institucion=institucion,
            clave_idempotencia='clave-dominio',
            datos=datos,
        )
        segunda = crear_solicitud_idempotente(
            institucion=institucion,
            clave_idempotencia='clave-dominio',
            datos=datos,
        )

        self.assertFalse(primera.repetida)
        self.assertTrue(segunda.repetida)
        self.assertEqual(primera.solicitud.pk, segunda.solicitud.pk)
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
