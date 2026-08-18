from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from instituciones.models import Institucion, MembresiaInstitucion
from instituciones.services.membresias import (
    activar_membresia,
    cambiar_rol_membresia,
    crear_membresia,
    desactivar_membresia,
    obtener_membresias_activas_usuario,
    resolver_institucion_activa,
)


class MembresiaInstitucionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(
            username='actor-institucional@example.test',
            email='actor-institucional@example.test',
            password='Clave-2026',
        )
        self.usuario = User.objects.create_user(
            username='operador@example.test',
            email='operador@example.test',
            password='Clave-2026',
        )
        self.institucion = Institucion.objects.create(
            nombre_comercial='Instituto Principal',
            razon_social='Instituto Principal SAS',
            numero_identificacion_tributaria='901500001',
        )

    def crear(self, **kwargs):
        datos = {
            'usuario': self.usuario,
            'institucion': self.institucion,
            'rol': MembresiaInstitucion.Rol.INSTITUTION_ANALYST,
            'actor': self.actor,
        }
        datos.update(kwargs)
        return crear_membresia(**datos)

    def test_creacion_valida_registra_actor_y_timestamps(self):
        membresia = self.crear()

        self.assertEqual(membresia.creado_por, self.actor)
        self.assertTrue(membresia.activa)
        self.assertIsNotNone(membresia.invitado_en)
        self.assertIsNotNone(membresia.activado_en)
        self.assertIsNone(membresia.desactivado_en)

    def test_creacion_idempotente_no_duplica_membresia(self):
        primera = self.crear()
        segunda = self.crear()

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(MembresiaInstitucion.objects.count(), 1)

    def test_duplicado_con_otro_rol_exige_operacion_explicita(self):
        self.crear()

        with self.assertRaises(ValidationError):
            self.crear(rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN)

        self.assertEqual(MembresiaInstitucion.objects.count(), 1)

    def test_restriccion_unica_existe_en_base_de_datos(self):
        self.crear()

        with self.assertRaises(IntegrityError), transaction.atomic():
            MembresiaInstitucion.objects.create(
                usuario=self.usuario,
                institucion=self.institucion,
                rol=MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY,
            )

    def test_institucion_inactiva_no_admite_membresia_activa(self):
        self.institucion.activa = False
        self.institucion.save(update_fields=['activa'])

        with self.assertRaises(ValidationError):
            self.crear()

        self.assertFalse(MembresiaInstitucion.objects.exists())

    def test_modelo_rechaza_acceso_activo_a_institucion_inactiva(self):
        self.institucion.activa = False
        self.institucion.save(update_fields=['activa'])
        membresia = MembresiaInstitucion(
            usuario=self.usuario,
            institucion=self.institucion,
            rol=MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY,
        )

        with self.assertRaises(ValidationError):
            membresia.full_clean()

    def test_rol_invalido_y_actor_ausente_son_rechazados(self):
        with self.assertRaises(ValidationError):
            self.crear(rol='ROL_INEXISTENTE')
        with self.assertRaises(ValidationError):
            self.crear(actor=None)

    def test_desactivar_y_reactivar_conserva_fechas_coherentes(self):
        membresia = self.crear()
        desactivada = desactivar_membresia(
            membresia=membresia,
            actor=self.actor,
        )
        fecha_desactivacion = desactivada.desactivado_en

        self.assertFalse(desactivada.activa)
        self.assertIsNotNone(fecha_desactivacion)
        self.assertEqual(
            desactivar_membresia(
                membresia=desactivada,
                actor=self.actor,
            ).desactivado_en,
            fecha_desactivacion,
        )

        reactivada = activar_membresia(
            membresia=desactivada,
            actor=self.actor,
        )
        self.assertTrue(reactivada.activa)
        self.assertIsNone(reactivada.desactivado_en)
        self.assertGreaterEqual(reactivada.activado_en, fecha_desactivacion)

    def test_no_reactiva_si_la_institucion_fue_desactivada(self):
        membresia = desactivar_membresia(
            membresia=self.crear(),
            actor=self.actor,
        )
        self.institucion.activa = False
        self.institucion.save(update_fields=['activa'])

        with self.assertRaises(ValidationError):
            activar_membresia(membresia=membresia, actor=self.actor)

    def test_cambio_de_rol_es_explicito_e_idempotente(self):
        membresia = self.crear()
        cambiada = cambiar_rol_membresia(
            membresia=membresia,
            rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
            actor=self.actor,
        )
        repetida = cambiar_rol_membresia(
            membresia=cambiada,
            rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
            actor=self.actor,
        )

        self.assertEqual(
            repetida.rol,
            MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
        )

    def test_usuario_con_varias_instituciones_resuelve_solo_seleccion_valida(self):
        primera = self.crear()
        segunda_institucion = Institucion.objects.create(
            nombre_comercial='Instituto Secundario',
            razon_social='Instituto Secundario SAS',
            numero_identificacion_tributaria='901500002',
        )
        segunda = self.crear(institucion=segunda_institucion)

        sin_seleccion = resolver_institucion_activa(usuario=self.usuario)
        seleccionada = resolver_institucion_activa(
            usuario=self.usuario,
            membresia_id=segunda.pk,
        )
        manipulada = resolver_institucion_activa(
            usuario=self.usuario,
            membresia_id=self.actor.pk,
        )

        self.assertTrue(sin_seleccion.requiere_seleccion)
        self.assertEqual(seleccionada.membresia.pk, segunda.pk)
        self.assertTrue(manipulada.seleccion_invalida)
        self.assertIsNone(manipulada.membresia)
        self.assertSetEqual(
            {m.pk for m in sin_seleccion.membresias},
            {primera.pk, segunda.pk},
        )

    def test_consulta_activa_evitar_n_mas_uno_y_excluye_usuario_inactivo(self):
        self.crear()
        segunda_institucion = Institucion.objects.create(
            nombre_comercial='Instituto Dos',
            razon_social='Instituto Dos SAS',
            numero_identificacion_tributaria='901500003',
        )
        self.crear(institucion=segunda_institucion)

        with CaptureQueriesContext(connection) as consultas:
            membresias = list(
                obtener_membresias_activas_usuario(usuario=self.usuario)
            )
            nombres = [m.institucion.nombre_comercial for m in membresias]

        self.assertEqual(len(consultas), 1)
        self.assertEqual(len(nombres), 2)

        self.usuario.is_active = False
        self.usuario.save(update_fields=['is_active'])
        self.assertFalse(
            obtener_membresias_activas_usuario(
                usuario=self.usuario
            ).exists()
        )

    def test_admin_crea_membresia_mediante_servicio_controlado(self):
        User = get_user_model()
        superusuario = User.objects.create_superuser(
            username='superadmin@example.test',
            email='superadmin@example.test',
            password='Clave-2026',
        )
        self.client.force_login(superusuario)

        respuesta = self.client.post(
            reverse('admin:instituciones_membresiainstitucion_add'),
            {
                'usuario': self.usuario.pk,
                'institucion': self.institucion.pk,
                'rol': MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
                'activa': 'on',
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        membresia = MembresiaInstitucion.objects.get()
        self.assertEqual(membresia.creado_por, superusuario)
        self.assertTrue(membresia.activa)
        pagina = self.client.get(
            reverse(
                'admin:instituciones_membresiainstitucion_change',
                args=[membresia.pk],
            )
        )
        self.assertContains(pagina, 'activado_en')
        self.assertNotContains(pagina, 'secreto_hash')
