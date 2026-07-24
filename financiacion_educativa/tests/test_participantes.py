from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from financiacion_educativa.choices import RolParticipante, TipoDocumentoIdentidad
from financiacion_educativa.models import RolParticipanteFinanciacion
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_adulto_como_estudiante_y_deudor,
    registrar_estudiante_menor_con_tutor,
)
from financiacion_educativa.tests.factories import crear_solicitud


class ParticipantesFinanciacionTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()

    def _datos_adulto(self, documento='10000001'):
        return DatosParticipante(
            nombres='LAURA',
            apellidos='GOMEZ',
            tipo_documento=TipoDocumentoIdentidad.CC,
            numero_documento=documento,
            fecha_nacimiento=date(1990, 5, 20),
            fecha_nacimiento_confirmada=True,
        )

    def _datos_menor(self):
        return DatosParticipante(
            nombres='JUAN',
            apellidos='GOMEZ',
            tipo_documento=TipoDocumentoIdentidad.TI,
            numero_documento='20000001',
            fecha_nacimiento=date(2012, 5, 20),
            fecha_nacimiento_confirmada=True,
        )

    def test_adulto_es_estudiante_y_deudor_principal(self):
        participante = registrar_adulto_como_estudiante_y_deudor(
            solicitud=self.solicitud,
            datos=self._datos_adulto(),
        )

        self.assertSetEqual(
            set(participante.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.assertTrue(participante.responsable_contractual)

    def test_menor_es_estudiante_y_tutor_adulto_es_deudor(self):
        estudiante, tutor = registrar_estudiante_menor_con_tutor(
            solicitud=self.solicitud,
            estudiante=self._datos_menor(),
            tutor=self._datos_adulto(),
        )

        self.assertSetEqual(
            set(estudiante.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT},
        )
        self.assertSetEqual(
            set(tutor.roles.values_list('rol', flat=True)),
            {RolParticipante.GUARDIAN, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.assertFalse(estudiante.responsable_contractual)
        self.assertTrue(tutor.responsable_contractual)

    def test_rechaza_tutor_menor_con_fecha_confirmada_y_revierte_todo(self):
        with self.assertRaises(ValidationError):
            registrar_estudiante_menor_con_tutor(
                solicitud=self.solicitud,
                estudiante=self._datos_menor(),
                tutor=DatosParticipante(
                    nombres='TUTOR',
                    apellidos='MENOR',
                    tipo_documento=TipoDocumentoIdentidad.TI,
                    numero_documento='30000001',
                    fecha_nacimiento=date(2010, 1, 1),
                    fecha_nacimiento_confirmada=True,
                ),
            )

        self.assertEqual(self.solicitud.participantes.count(), 0)

    def test_rechaza_roles_estudiante_y_tutor_en_misma_persona(self):
        participante = registrar_adulto_como_estudiante_y_deudor(
            solicitud=self.solicitud,
            datos=self._datos_adulto(),
        )
        rol_invalido = RolParticipanteFinanciacion(
            solicitud=self.solicitud,
            participante=participante,
            rol=RolParticipante.GUARDIAN,
        )

        with self.assertRaises(ValidationError):
            rol_invalido.full_clean()
