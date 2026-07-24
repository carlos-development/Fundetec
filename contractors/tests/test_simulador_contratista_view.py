from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from contractors.models import (
    ContractorApplication,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ConfiguracionPortalContratistas,
)
from gestion_creditos.models import Credito, CreditoLibranza, HistorialEstado, HistorialPago


@override_settings(
    PRIMARY_DOMAIN_HOST='aprobado.com.co',
    CONTRACTORS_PORTAL_HOST='contratistas.aprobado.com.co',
    ALLOWED_HOSTS=['.aprobado.com.co', 'testserver'],
)
class SimuladorContratistaViewTests(TestCase):
    def setUp(self):
        self.organizacion = ContractorOrganization.objects.create(
            name='Acme Contractors',
            slug='acme',
            subdomain='contratistas',
        )
        self.otra_organizacion = ContractorOrganization.objects.create(
            name='Beta Contractors',
            slug='beta',
            subdomain='beta',
        )
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Acme Credito',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            color_primario='#112233',
            color_secundario='#445566',
            texto_landing='Credito para contratistas Acme.',
            monto_minimo=Decimal('100000.00'),
            monto_maximo=Decimal('5000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('100000.00'),
            tasa_iva=Decimal('19.0000'),
        )
        ContractorBranding.objects.create(
            organization=self.organizacion,
            display_name='Acme Credito',
            primary_color='#112233',
            secondary_color='#445566',
            landing_copy='Credito para contratistas Acme.',
        )
        ContractorBranding.objects.create(
            organization=self.otra_organizacion,
            display_name='Beta Credito',
            primary_color='#778899',
            secondary_color='#aabbcc',
            landing_copy='Credito para contratistas Beta.',
        )
        self.configuracion = ContractorProductConfig.objects.create(
            organization=self.organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('100000.00'),
            max_amount=Decimal('5000000.00'),
            min_term_months=3,
            max_term_months=24,
            monthly_rate=Decimal('2.5000'),
            commission_rate=Decimal('5.0000'),
            commission_amount=Decimal('100000.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.otra_configuracion = ContractorProductConfig.objects.create(
            organization=self.otra_organizacion,
            product_type=ContractorProductConfig.ProductType.CONTRACTOR_CREDIT,
            min_amount=Decimal('50000.00'),
            max_amount=Decimal('2000000.00'),
            min_term_months=1,
            max_term_months=12,
            monthly_rate=Decimal('7.0000'),
            commission_rate=Decimal('1.0000'),
            commission_amount=Decimal('0.00'),
            vat_rate=Decimal('19.0000'),
        )
        self.solicitud = ContractorApplication.objects.create(
            organization=None,
            configuracion_portal=self.configuracion_portal,
            product_config=None,
            status=ContractorApplication.Estado.RECIBIDA,
            requested_amount=Decimal('1000000.00'),
            term_months=12,
            estimated_monthly_payment=Decimal('114888.58'),
            simulation_payload={'cuota_mensual': '114888.58'},
            document_type='CC',
            document_number='123456789',
            first_name='Ana',
            last_name='Perez',
            phone='3001234567',
            email='ana@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )
        self.usuario = get_user_model().objects.create_user(
            username='contratista-simulador',
            email='contratista-simulador@example.com',
            password='password-test',
        )
        self.solicitud.usuario = self.usuario
        self.solicitud.save(update_fields=['usuario'])
        self.client.force_login(self.usuario)

    def _url(self, solicitud=None):
        solicitud = solicitud or self.solicitud
        return f'/simular/?solicitud_id={solicitud.id}'

    def test_subdominio_valido_ve_formulario(self):
        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acme Credito')
        self.assertContains(response, 'Monto a simular')
        self.assertContains(response, 'Plazo en meses')

    def test_usuario_no_puede_simular_solicitud_de_otro_usuario(self):
        otro_usuario = get_user_model().objects.create_user(
            username='otro-contratista-simulador',
            email='otro-simulador@example.com',
            password='password-test',
        )
        self.solicitud.usuario = otro_usuario
        self.solicitud.save(update_fields=['usuario'])

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_simulador_sin_solicitud_redirige_a_solicitud(self):
        response = self.client.get('/simular/', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_anonimo_redirige_a_login(self):
        self.client.logout()

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/login/?next=/simular/%3Fsolicitud_id%3D{self.solicitud.id}')

    def test_subdominio_inexistente_devuelve_404(self):
        response = self.client.get(self._url(), HTTP_HOST='inexistente.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_organizacion_sin_configuracion_activa_devuelve_404(self):
        self.solicitud.delete()
        self.configuracion_portal.delete()

        response = self.client.get('/simular/?solicitud_id=999999', HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_configuracion_inactiva_no_se_usa(self):
        self.configuracion_portal.activo = False
        self.configuracion_portal.save(update_fields=['activo'])

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 404)

    def test_organizacion_inactiva_devuelve_404(self):
        self.configuracion_portal.activo = False
        self.configuracion_portal.save(update_fields=['activo'])

        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 404)

    def test_post_valido_muestra_simulacion(self):
        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resultado de simulacion')
        self.assertEqual(response.context['resultado'].capital_financiado, Decimal('1178500.00'))
        self.assertEqual(response.context['resultado'].tasa_mensual, Decimal('2.5000'))
        self.assertContains(response, '1.178.500,00')
        self.assertContains(response, '2,5000%')

    def test_simulador_funciona_sin_product_config_legacy(self):
        ContractorProductConfig.objects.all().delete()
        ContractorBranding.objects.all().delete()
        ContractorOrganization.objects.all().delete()

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['resultado'].configuracion_portal_id, self.configuracion_portal.id)
        self.assertEqual(response.context['resultado'].organizacion_id, None)
        self.assertEqual(response.context['resultado'].configuracion_producto_id, None)

    def test_simulador_usa_condiciones_de_configuracion_portal(self):
        self.configuracion.monthly_rate = Decimal('9.0000')
        self.configuracion.commission_rate = Decimal('1.0000')
        self.configuracion.commission_amount = Decimal('0.00')
        self.configuracion.vat_rate = Decimal('0.0000')
        self.configuracion.save(update_fields=['monthly_rate', 'commission_rate', 'commission_amount', 'vat_rate'])
        self.configuracion_portal.tasa_mensual = Decimal('3.0000')
        self.configuracion_portal.tasa_comision = Decimal('10.0000')
        self.configuracion_portal.comision_fija = Decimal('50000.00')
        self.configuracion_portal.tasa_iva = Decimal('19.0000')
        self.configuracion_portal.save(update_fields=['tasa_mensual', 'tasa_comision', 'comision_fija', 'tasa_iva'])

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        resultado = response.context['resultado']
        self.assertEqual(resultado.tasa_mensual, Decimal('3.0000'))
        self.assertEqual(resultado.comision, Decimal('150000.00'))
        self.assertEqual(resultado.iva_comision, Decimal('28500.00'))

    def test_simulador_valida_limites_desde_configuracion_portal(self):
        self.configuracion.min_amount = Decimal('100000.00')
        self.configuracion.max_amount = Decimal('5000000.00')
        self.configuracion.min_term_months = 1
        self.configuracion.max_term_months = 36
        self.configuracion.save(update_fields=['min_amount', 'max_amount', 'min_term_months', 'max_term_months'])
        self.configuracion_portal.monto_minimo = Decimal('500000.00')
        self.configuracion_portal.monto_maximo = Decimal('2000000.00')
        self.configuracion_portal.plazo_minimo_meses = 6
        self.configuracion_portal.plazo_maximo_meses = 12
        self.configuracion_portal.save(
            update_fields=['monto_minimo', 'monto_maximo', 'plazo_minimo_meses', 'plazo_maximo_meses'],
        )

        response = self.client.post(
            self._url(),
            {'monto': '3000000.00', 'plazo_meses': '18'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'monto_supera_maximo')
        self.assertNotContains(response, 'Resultado de simulacion')

    def test_post_invalido_no_simula(self):
        response = self.client.post(
            self._url(),
            {'monto': '50000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'monto_menor_al_minimo')
        self.assertNotContains(response, 'Resultado de simulacion')

    def test_simulacion_usa_configuracion_del_contratista_a(self):
        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['resultado'].tasa_mensual, Decimal('2.5000'))
        self.assertEqual(response.context['resultado'].comision, Decimal('150000.00'))
        self.assertContains(response, '2,5000%')
        self.assertContains(response, '150.000,00')

    def test_simulacion_no_mezcla_configuracion_del_contratista_b(self):
        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '7,0000%')
        self.assertNotContains(response, 'Beta Credito')

    def test_contratista_a_no_usa_branding_de_contratista_b(self):
        response = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acme Credito')
        self.assertNotContains(response, 'Beta Credito')

    def test_middleware_no_expone_organizaciones_heredadas_por_subdominio(self):
        response_acme = self.client.get(self._url(), HTTP_HOST='contratistas.aprobado.com.co')
        response_beta = self.client.get(self._url(), HTTP_HOST='beta.aprobado.com.co')

        self.assertEqual(response_acme.status_code, 200)
        self.assertNotEqual(response_beta.status_code, 200)
        self.assertEqual(response_acme.context['configuracion_portal'], self.configuracion_portal)

    def test_post_subdominio_empresa_no_expone_simulador_contratista(self):
        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='beta.aprobado.com.co',
        )

        self.assertNotEqual(response.status_code, 200)

    def test_no_crea_credito(self):
        creditos_antes = Credito.objects.count()

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.count(), creditos_antes)

    def test_no_crea_credito_libranza(self):
        detalles_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CreditoLibranza.objects.count(), detalles_antes)

    def test_post_valido_no_escribe_modelos_del_flujo(self):
        conteos_antes = {
            'credito': Credito.objects.count(),
            'credito_libranza': CreditoLibranza.objects.count(),
            'historial_estado': HistorialEstado.objects.count(),
            'historial_pago': HistorialPago.objects.count(),
            'organizacion': ContractorOrganization.objects.count(),
            'branding': ContractorBranding.objects.count(),
            'configuracion': ContractorProductConfig.objects.count(),
        }

        response = self.client.post(
            self._url(),
            {'monto': '1000000.00', 'plazo_meses': '12'},
            HTTP_HOST='contratistas.aprobado.com.co',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Credito.objects.count(), conteos_antes['credito'])
        self.assertEqual(CreditoLibranza.objects.count(), conteos_antes['credito_libranza'])
        self.assertEqual(HistorialEstado.objects.count(), conteos_antes['historial_estado'])
        self.assertEqual(HistorialPago.objects.count(), conteos_antes['historial_pago'])
        self.assertEqual(ContractorOrganization.objects.count(), conteos_antes['organizacion'])
        self.assertEqual(ContractorBranding.objects.count(), conteos_antes['branding'])
        self.assertEqual(ContractorProductConfig.objects.count(), conteos_antes['configuracion'])

    def test_dominio_raiz_no_expone_simulador_contratista(self):
        response = self.client.get(self._url(), HTTP_HOST='aprobado.com.co')

        self.assertEqual(response.status_code, 404)

