from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.idempotencia import (
    crear_solicitud_idempotente,
)
from financiacion_educativa.services.solicitudes import DatosSolicitudFinanciacion
from instituciones.models import Institucion


class IdempotenciaConcurrenteTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature('has_select_for_update')
    def test_solicitudes_concurrentes_no_duplican(self):
        institucion = Institucion.objects.create(
            nombre_comercial='Institucion concurrente',
            razon_social='Institucion concurrente SAS',
            numero_identificacion_tributaria='901999999',
        )
        datos = DatosSolicitudFinanciacion(
            referencia_externa='CONCURRENT-001',
            nombres='JUAN',
            apellidos='PEREZ',
            celular='3001234567',
            correo='juan@example.com',
            direccion='Calle 1',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=6,
            nombre_curso='Programacion',
        )

        def crear():
            close_old_connections()
            institucion_local = Institucion.objects.get(pk=institucion.pk)
            resultado = crear_solicitud_idempotente(
                institucion=institucion_local,
                clave_idempotencia='concurrent-key',
                datos=datos,
            )
            close_old_connections()
            return resultado.solicitud.pk

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            ids = list(ejecutor.map(lambda _: crear(), range(2)))

        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
