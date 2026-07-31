from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from contractors.models import ContractorApplication
from gestion_creditos.models import Credito, Pagare
from usuarios.models import ProductAccessProfile


LEGACY_PATHS = (
    '/libranza/',
    '/gestion/',
    '/pagador/',
    '/ejecutivos/',
    '/asesores/',
    '/billetera/',
    '/inversionista/',
    '/webhook/wompi/events/',
    '/api/webhooks/zapsign/',
    '/api/pagares/download/token-historico/',
    '/media/contractors/applications/documents/documento.pdf',
    '/media/pagares/2026/01/pagare.pdf',
    '/media/pagares_firmados/2026/01/pagare.pdf',
)


def _walk_urlpatterns(urlpatterns):
    for entry in urlpatterns:
        if isinstance(entry, URLPattern):
            yield entry
        elif isinstance(entry, URLResolver):
            yield from _walk_urlpatterns(entry.url_patterns)


@override_settings(
    ALLOWED_HOSTS=[
        'testserver',
        'aprobado.com.co',
        'contratistas.aprobado.com.co',
        'emprender.aprobado.com.co',
        'market.aprobado.com.co',
    ],
)
class PublicSurfaceCharacterizationTests(TestCase):
    def test_root_is_educational_institutional_page(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'financiacion_educativa/institucional.html',
        )
        self.assertContains(response, 'Impulsamos tus sue&ntilde;os.')
        self.assertContains(response, 'Continuar mi solicitud')
        self.assertNotContains(response, 'FUNDETEC')

    def test_educational_and_institutional_routes_remain_available(self):
        expected_statuses = (
            (reverse('financiacion_educativa_web:inicio'), 410),
            (
                reverse(
                    'financiacion_educativa_web:continuar-invitacion',
                    kwargs={'token': 'token-invalido'},
                ),
                410,
            ),
            (reverse('financiacion_educativa_api:solicitud-crear'), 401),
            (reverse('api-schema'), 200),
            (reverse('account_login'), 200),
            (reverse('admin:index'), 302),
        )

        for url, expected_status in expected_statuses:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected_status)

    def test_legacy_routes_and_public_integrations_are_absent(self):
        for path in LEGACY_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_legacy_subdomains_are_not_served(self):
        for host in (
            'contratistas.aprobado.com.co',
            'emprender.aprobado.com.co',
            'market.aprobado.com.co',
        ):
            with self.subTest(host=host):
                response = self.client.get('/', HTTP_HOST=host)
                self.assertEqual(response.status_code, 404)

    def test_complete_url_resolver_loads_without_legacy_public_callbacks(self):
        patterns = list(_walk_urlpatterns(get_resolver().url_patterns))

        self.assertGreater(len(patterns), 0)
        for pattern in patterns:
            self.assertTrue(callable(pattern.callback))

        public_callbacks = {
            pattern.callback.__module__
            for pattern in patterns
            if not str(pattern.pattern).startswith('admin/')
        }
        forbidden_prefixes = (
            'contractors.',
            'gestion_creditos.',
            'usuariocreditos.',
            'usuarios.views',
        )
        self.assertFalse(
            any(
                module.startswith(forbidden_prefixes)
                for module in public_callbacks
            )
        )

    def test_education_renders_without_usuarios_context_processors(self):
        configured = settings.TEMPLATES[0]['OPTIONS']['context_processors']

        self.assertFalse(
            any(path.startswith('usuarios.context_processors') for path in configured)
        )
        self.assertIn(
            'aprobado_web.context_processors.brand_processor',
            configured,
        )
        configured_middleware = settings.MIDDLEWARE
        self.assertFalse(
            any(
                path.startswith(
                    (
                        'contractors.',
                        'usuarios.middleware',
                        'aprobado_web.middleware.SubdomainRoutingMiddleware',
                    )
                )
                for path in configured_middleware
            )
        )
        response = self.client.get(
            reverse(
                'financiacion_educativa_web:continuar-invitacion',
                kwargs={'token': 'token-invalido'},
            )
        )
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'Aprobado', status_code=410)
        self.assertNotContains(response, 'FUNDETEC', status_code=410)


class RegistryAndPersistenceCharacterizationTests(TestCase):
    def test_expected_apps_remain_registered(self):
        for app_label in (
            'gestion_creditos',
            'usuarios',
            'contractors',
            'instituciones',
            'financiacion_educativa',
            'usuariocreditos',
            'configuraciones',
        ):
            with self.subTest(app_label=app_label):
                self.assertTrue(apps.is_installed(app_label))

    def test_historical_models_and_tables_are_preserved(self):
        expected_models = (
            ('gestion_creditos', 'Credito'),
            ('gestion_creditos', 'Pagare'),
            ('contractors', 'ContractorApplication'),
            ('usuarios', 'ProductAccessProfile'),
        )
        table_names = set(connection.introspection.table_names())

        for app_label, model_name in expected_models:
            with self.subTest(model=f'{app_label}.{model_name}'):
                model = apps.get_model(app_label, model_name)
                self.assertIsNotNone(model)
                self.assertIn(model._meta.db_table, table_names)


class HistoricalAdminCharacterizationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='superuser-fase-65',
            email='superuser@example.com',
            password='test-password',
        )
        self.staff_user = User.objects.create_user(
            username='staff-fase-65',
            email='staff@example.com',
            password='test-password',
            is_staff=True,
        )

    def _request_for(self, user):
        request = self.factory.get('/admin/')
        request.user = user
        return request

    def test_historical_admin_is_read_only_for_superusers(self):
        request = self._request_for(self.superuser)

        for model in (Credito, Pagare, ContractorApplication, ProductAccessProfile):
            with self.subTest(model=model._meta.label):
                model_admin = admin.site._registry[model]
                self.assertTrue(model_admin.has_module_permission(request))
                self.assertTrue(model_admin.has_view_permission(request))
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))
                self.assertEqual(model_admin.get_actions(request), {})

    def test_historical_admin_is_hidden_from_non_superuser_staff(self):
        request = self._request_for(self.staff_user)

        for model in (Credito, ContractorApplication, ProductAccessProfile):
            with self.subTest(model=model._meta.label):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_module_permission(request))
                self.assertFalse(model_admin.has_view_permission(request))

    def test_historical_admin_http_access_matches_read_only_policy(self):
        changelist_url = reverse('admin:gestion_creditos_credito_changelist')
        add_url = reverse('admin:gestion_creditos_credito_add')

        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.get(changelist_url).status_code, 403)

        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(changelist_url).status_code, 200)
        self.assertEqual(self.client.get(add_url).status_code, 403)


class CeleryCharacterizationTests(TestCase):
    def test_no_legacy_periodic_tasks_are_active(self):
        from aprobado_web.celery import app as celery_app

        self.assertFalse(hasattr(settings, 'CELERY_BEAT_SCHEDULER'))
        configured_schedules = (
            getattr(settings, 'CELERY_BEAT_SCHEDULE', {}) or {},
            celery_app.conf.beat_schedule or {},
        )
        for schedule in configured_schedules:
            with self.subTest(schedule=schedule):
                self.assertFalse(
                    any(
                        str(entry.get('task', '')).startswith(
                            'gestion_creditos.tasks.'
                        )
                        for entry in schedule.values()
                    )
                )
