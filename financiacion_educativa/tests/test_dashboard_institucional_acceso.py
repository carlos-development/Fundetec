from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from financiacion_educativa.dashboards.institucional.permissions import (
    SESSION_MEMBRESIA_INSTITUCIONAL_ID,
)
from financiacion_educativa.tests.factories import crear_solicitud
from instituciones.models import Institucion, MembresiaInstitucion
from instituciones.services.membresias import (
    crear_membresia,
    desactivar_membresia,
)


class DashboardInstitucionalAccesoTests(TestCase):
    ETIQUETAS_ROL_PROGRAMA = {
        MembresiaInstitucion.Rol.INSTITUTION_ADMIN: (
            'Administrador de programa'
        ),
        MembresiaInstitucion.Rol.INSTITUTION_ANALYST: (
            'Analista de programa'
        ),
        MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY: (
            'Consulta del programa'
        ),
    }

    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(
            username='actor-dashboard@example.test',
            email='actor-dashboard@example.test',
            password='Clave-2026',
        )
        self.usuario = User.objects.create_user(
            username='institucional@example.test',
            email='institucional@example.test',
            password='Clave-2026',
        )
        self.institucion = self._crear_institucion('Principal', '901600001')
        self.inicio = reverse(
            'financiacion_educativa_web:institucion:inicio'
        )
        self.seleccionar = reverse(
            'financiacion_educativa_web:institucion:seleccionar'
        )
        self.cambiar = reverse(
            'financiacion_educativa_web:institucion:cambiar'
        )

    def _crear_institucion(self, nombre, nit):
        return Institucion.objects.create(
            nombre_comercial=f'Instituto {nombre}',
            razon_social=f'Instituto {nombre} SAS',
            numero_identificacion_tributaria=nit,
        )

    def _crear_membresia(
        self,
        *,
        usuario=None,
        institucion=None,
        rol=MembresiaInstitucion.Rol.INSTITUTION_ANALYST,
    ):
        return crear_membresia(
            usuario=usuario or self.usuario,
            institucion=institucion or self.institucion,
            rol=rol,
            actor=self.actor,
        )

    def _seleccionar_en_sesion(self, membresia):
        sesion = self.client.session
        sesion[SESSION_MEMBRESIA_INSTITUCIONAL_ID] = str(membresia.pk)
        sesion.save()

    def test_namespace_y_rutas_son_los_esperados(self):
        self.assertEqual(
            self.inicio,
            '/financiacion-educativa/institucion/',
        )
        self.assertEqual(
            self.seleccionar,
            '/financiacion-educativa/institucion/seleccionar/',
        )
        self.assertEqual(
            self.cambiar,
            '/financiacion-educativa/institucion/cambiar/',
        )

    def test_anonimo_es_redirigido_a_login_sin_datos(self):
        for metodo, url in (
            ('get', self.inicio),
            ('get', self.seleccionar),
            ('post', self.cambiar),
        ):
            with self.subTest(url=url):
                respuesta = getattr(self.client, metodo)(url)
                self.assertEqual(respuesta.status_code, 302)
                self.assertIn('/accounts/login/', respuesta.url)
                self.assertNotContains(
                    respuesta,
                    self.institucion.nombre_comercial,
                    status_code=302,
                )

    def test_usuario_sin_membresia_recibe_respuesta_controlada(self):
        self.client.force_login(self.usuario)

        self.assertEqual(self.client.get(self.inicio).status_code, 403)
        self.assertEqual(self.client.get(self.seleccionar).status_code, 403)
        self.assertEqual(self.client.post(self.cambiar).status_code, 403)

    def test_staff_y_superuser_no_obtienen_acceso_implicito(self):
        User = get_user_model()
        for usuario in (
            User.objects.create_user(
                username='staff-dashboard@example.test',
                email='staff-dashboard@example.test',
                password='Clave-2026',
                is_staff=True,
            ),
            User.objects.create_superuser(
                username='root-dashboard@example.test',
                email='root-dashboard@example.test',
                password='Clave-2026',
            ),
        ):
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.inicio).status_code, 403)
                self.client.logout()

    def test_solicitante_normal_no_se_convierte_en_operador(self):
        solicitante = get_user_model().objects.create_user(
            username='solicitante-dashboard@example.test',
            email='solicitante-dashboard@example.test',
            password='Clave-2026',
        )
        crear_solicitud(
            institucion=self.institucion,
            usuario=solicitante,
            correo=solicitante.email,
        )
        self.client.force_login(solicitante)

        self.assertEqual(self.client.get(self.inicio).status_code, 403)

    def test_membresia_inactiva_no_concede_acceso(self):
        membresia = desactivar_membresia(
            membresia=self._crear_membresia(),
            actor=self.actor,
        )
        self._seleccionar_en_sesion(membresia)
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self.inicio)

        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn(
            SESSION_MEMBRESIA_INSTITUCIONAL_ID,
            self.client.session,
        )

    def test_institucion_desactivada_revoca_contexto_en_siguiente_request(self):
        membresia = self._crear_membresia()
        self.client.force_login(self.usuario)
        self._seleccionar_en_sesion(membresia)
        self.institucion.activa = False
        self.institucion.save(update_fields=['activa'])

        respuesta = self.client.get(self.inicio)

        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn(
            SESSION_MEMBRESIA_INSTITUCIONAL_ID,
            self.client.session,
        )

    def test_una_membresia_se_selecciona_automaticamente_para_cada_rol(self):
        for indice, rol in enumerate(MembresiaInstitucion.Rol.values, start=1):
            usuario = get_user_model().objects.create_user(
                username=f'rol-{indice}@example.test',
                email=f'rol-{indice}@example.test',
                password='Clave-2026',
            )
            membresia = self._crear_membresia(usuario=usuario, rol=rol)
            self.client.force_login(usuario)

            respuesta = self.client.get(self.inicio)

            self.assertEqual(respuesta.status_code, 200)
            self.assertContains(
                respuesta,
                self.ETIQUETAS_ROL_PROGRAMA[rol],
            )
            self.assertNotContains(respuesta, membresia.get_rol_display())
            self.assertEqual(
                self.client.session[SESSION_MEMBRESIA_INSTITUCIONAL_ID],
                str(membresia.pk),
            )
            self.client.logout()

    def test_varias_membresias_exigen_selector_sin_exponer_inactivas(self):
        primera = self._crear_membresia()
        segunda_institucion = self._crear_institucion(
            'Secundario',
            '901600002',
        )
        segunda = self._crear_membresia(institucion=segunda_institucion)
        inactiva_institucion = self._crear_institucion(
            'Oculto',
            '901600003',
        )
        inactiva = self._crear_membresia(institucion=inactiva_institucion)
        desactivar_membresia(membresia=inactiva, actor=self.actor)
        self.client.force_login(self.usuario)

        inicio = self.client.get(self.inicio)
        selector = self.client.get(self.seleccionar)

        self.assertRedirects(inicio, self.seleccionar)
        self.assertContains(selector, primera.institucion.nombre_comercial)
        self.assertContains(selector, segunda.institucion.nombre_comercial)
        self.assertNotContains(selector, inactiva_institucion.nombre_comercial)
        self.assertContains(selector, 'Selecciona un programa')
        self.assertContains(selector, 'Programas disponibles')
        self.assertContains(selector, 'Acceso a programas')
        self.assertNotContains(selector, 'Selecciona una instituci&oacute;n')
        self.assertNotContains(selector, 'Instituciones disponibles')

    def test_selector_post_valida_membresia_y_guarda_solo_su_id(self):
        self._crear_membresia()
        segunda_institucion = self._crear_institucion(
            'Seleccionado',
            '901600004',
        )
        segunda = self._crear_membresia(institucion=segunda_institucion)
        self.client.force_login(self.usuario)

        respuesta = self.client.post(
            self.seleccionar,
            {'institucion_id': str(segunda_institucion.pk)},
        )

        self.assertRedirects(respuesta, self.inicio)
        sesion = self.client.session
        self.assertEqual(
            sesion[SESSION_MEMBRESIA_INSTITUCIONAL_ID],
            str(segunda.pk),
        )
        self.assertNotIn('institucion_id', sesion)
        pagina = self.client.get(self.inicio)
        self.assertContains(pagina, segunda_institucion.nombre_comercial)
        self.assertContains(pagina, 'Programa activo')
        self.assertContains(pagina, 'Contexto de programa activo')
        self.assertContains(pagina, 'Cambiar programa')

    def test_uuid_ajeno_o_inexistente_no_enumera_y_limpia_sesion(self):
        propia = self._crear_membresia()
        segunda_institucion = self._crear_institucion('Otra', '901600005')
        self._crear_membresia(institucion=segunda_institucion)
        usuario_ajeno = get_user_model().objects.create_user(
            username='ajeno-dashboard@example.test',
            email='ajeno-dashboard@example.test',
            password='Clave-2026',
        )
        institucion_ajena = self._crear_institucion('Secreta', '901600006')
        membresia_ajena = self._crear_membresia(
            usuario=usuario_ajeno,
            institucion=institucion_ajena,
        )
        self.client.force_login(self.usuario)

        for identificador in (institucion_ajena.pk, uuid4()):
            with self.subTest(identificador=identificador):
                self._seleccionar_en_sesion(membresia_ajena)
                respuesta = self.client.post(
                    self.seleccionar,
                    {'institucion_id': str(identificador)},
                )
                self.assertEqual(respuesta.status_code, 400)
                self.assertContains(
                    respuesta,
                    'No fue posible seleccionar ese programa',
                    status_code=400,
                )
                self.assertNotContains(
                    respuesta,
                    institucion_ajena.nombre_comercial,
                    status_code=400,
                )
                self.assertNotIn(
                    SESSION_MEMBRESIA_INSTITUCIONAL_ID,
                    self.client.session,
                )
        self.assertTrue(propia.activa)

    def test_sesion_manipulada_se_revalida_antes_de_mostrar_contexto(self):
        self._crear_membresia()
        segunda = self._crear_membresia(
            institucion=self._crear_institucion('Dos', '901600007')
        )
        self.client.force_login(self.usuario)
        sesion = self.client.session
        sesion[SESSION_MEMBRESIA_INSTITUCIONAL_ID] = str(uuid4())
        sesion.save()

        respuesta = self.client.get(self.inicio)

        self.assertRedirects(respuesta, self.seleccionar)
        self.assertNotIn(
            SESSION_MEMBRESIA_INSTITUCIONAL_ID,
            self.client.session,
        )
        self.assertTrue(segunda.activa)

    def test_cambio_de_contexto_solo_admite_post(self):
        self._crear_membresia()
        self._crear_membresia(
            institucion=self._crear_institucion('Cambio', '901600008')
        )
        self.client.force_login(self.usuario)

        self.assertEqual(self.client.get(self.cambiar).status_code, 405)
        respuesta = self.client.post(self.cambiar)
        self.assertRedirects(respuesta, self.seleccionar)
        self.assertNotIn(
            SESSION_MEMBRESIA_INSTITUCIONAL_ID,
            self.client.session,
        )

    def test_selector_y_cambio_exigen_csrf(self):
        self._crear_membresia()
        self._crear_membresia(
            institucion=self._crear_institucion('CSRF', '901600009')
        )
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)

        self.assertEqual(
            cliente.post(
                self.seleccionar,
                {'institucion_id': str(self.institucion.pk)},
            ).status_code,
            403,
        )
        self.assertEqual(cliente.post(self.cambiar).status_code, 403)

    def test_revocacion_durante_sesion_impide_acceso_inmediato(self):
        membresia = self._crear_membresia()
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.inicio).status_code, 200)
        desactivar_membresia(membresia=membresia, actor=self.actor)

        respuesta = self.client.get(self.inicio)

        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn(
            SESSION_MEMBRESIA_INSTITUCIONAL_ID,
            self.client.session,
        )

    def test_shell_resume_sin_exponer_contacto_del_solicitante(self):
        self._crear_membresia(
            rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN
        )
        solicitud = crear_solicitud(
            institucion=self.institucion,
            referencia='DASH-NO-MOSTRAR',
            correo='persona-sensible@example.test',
        )
        self.client.force_login(self.usuario)

        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(self.inicio)

        self.assertEqual(respuesta.status_code, 200)
        self.assertLessEqual(len(consultas), 10)
        self.assertContains(respuesta, 'financiacion_educativa_dashboard.css')
        self.assertContains(respuesta, 'edu-dashboard-sidebar')
        self.assertContains(respuesta, 'Pr&oacute;ximamente', count=4, html=True)
        self.assertContains(respuesta, self.institucion.nombre_comercial)
        self.assertContains(respuesta, solicitud.referencia_externa)
        self.assertNotContains(respuesta, solicitud.correo)
        self.assertNotContains(respuesta, self.usuario.email)
        self.assertContains(respuesta, 'Mi cuenta')
        self.assertContains(respuesta, 'Indicadores del programa')
        self.assertNotContains(respuesta, 'capital financiado')
        self.assertContains(respuesta, 'id="contenido-principal"')
        self.assertContains(respuesta, 'aria-label="Navegaci&oacute;n del programa"')
        self.assertContains(respuesta, 'Panel del programa')
        self.assertNotContains(respuesta, 'Panel institucional')

    def test_selector_evita_n_mas_uno(self):
        self._crear_membresia()
        self._crear_membresia(
            institucion=self._crear_institucion('Query', '901600010')
        )
        self.client.force_login(self.usuario)

        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(self.seleccionar)
            self.assertContains(respuesta, 'Instituto Query')

        self.assertLessEqual(len(consultas), 6)

    def test_navbar_educativo_existente_no_es_reemplazado(self):
        respuesta = self.client.get(reverse('home'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Navegaci&oacute;n principal')
        self.assertNotContains(respuesta, 'edu-dashboard-sidebar')
        self.assertNotContains(
            respuesta,
            'financiacion_educativa_dashboard.css',
        )
