from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoIdentidad,
    TipoEventoParticipante,
)
from financiacion_educativa.models import (
    CondicionesFinancieras,
    EventoParticipanteFinanciacion,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    calcular_edad,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
)
from financiacion_educativa.tests.factories import crear_solicitud


class ParticipantesFase4Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='titular@example.com',
            email='titular@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='otro-f4@example.com',
            email='otro-f4@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud()
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['usuario', 'estado'])

    def _datos(self, documento='1.001-002', nacimiento=date(1990, 1, 1)):
        return DatosParticipante(
            nombres='  Ana   Maria ',
            apellidos=' Perez ',
            tipo_documento=TipoDocumentoIdentidad.CC,
            numero_documento=documento,
            fecha_nacimiento=nacimiento,
            correo=' ANA@EXAMPLE.COM ',
            telefono='300 123 4567',
            relacion_estudiante=RelacionEstudiante.SELF,
            pais_expedicion='co',
        )

    def test_normaliza_y_registra_varios_roles_sin_crear_obligaciones(self):
        participante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=self._datos(),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

        self.assertEqual(participante.numero_documento, '1001002')
        self.assertEqual(participante.correo, 'ana@example.com')
        self.assertEqual(participante.pais_expedicion, 'CO')
        self.assertSetEqual(
            set(participante.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.assertFalse(participante.identidad_verificada)
        self.assertFalse(participante.relacion_verificada)
        self.assertFalse(CondicionesFinancieras.objects.exists())

    def test_reintento_no_duplica_participante(self):
        primero = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=self._datos(),
            roles={RolParticipante.STUDENT},
        )
        segundo = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=self._datos(documento='1001002'),
            roles={RolParticipante.STUDENT},
        )

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(self.solicitud.participantes.count(), 1)
        self.assertEqual(EventoParticipanteFinanciacion.objects.count(), 1)

    def test_roles_permanecen_separados_y_son_corregibles(self):
        participante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=self._datos(),
            roles={RolParticipante.STUDENT},
        )
        actualizado = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            participante_id=participante.pk,
            datos=self._datos(),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

        self.assertSetEqual(
            set(actualizado.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.assertTrue(actualizado.responsable_contractual)
        self.assertEqual(EventoParticipanteFinanciacion.objects.count(), 2)
        campos = EventoParticipanteFinanciacion.objects.get(
            tipo=TipoEventoParticipante.UPDATED,
        ).campos_modificados
        self.assertEqual(campos, ['responsable_contractual', 'roles'])

    def test_rechaza_estudiante_y_tutor_en_la_misma_persona(self):
        with self.assertRaises(ValidationError):
            registrar_o_actualizar_participante(
                solicitud=self.solicitud,
                actor=self.usuario,
                datos=self._datos(),
                roles={RolParticipante.STUDENT, RolParticipante.GUARDIAN},
            )

    def test_otro_usuario_no_modifica_participantes(self):
        with self.assertRaises(ValidationError):
            registrar_o_actualizar_participante(
                solicitud=self.solicitud,
                actor=self.otro,
                datos=self._datos(),
                roles={RolParticipante.STUDENT},
            )
        self.assertFalse(self.solicitud.participantes.exists())

    def test_edad_en_fecha_limite(self):
        nacimiento = date(2008, 7, 24)
        self.assertEqual(calcular_edad(nacimiento, date(2026, 7, 23)), 17)
        self.assertEqual(calcular_edad(nacimiento, date(2026, 7, 24)), 18)

    @override_settings(FINANCIACION_EDUCATIVA_MAYORIA_EDAD=18)
    def test_estudiante_menor_genera_requisito_de_tutor(self):
        registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=self._datos(nacimiento=date(2012, 1, 1)),
            roles={RolParticipante.STUDENT},
        )

        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }
        self.assertFalse(requisitos['GUARDIAN'].cumplido)
