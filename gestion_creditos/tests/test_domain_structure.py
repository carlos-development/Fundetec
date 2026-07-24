from importlib import import_module

from django.test import SimpleTestCase


class DomainStructureTests(SimpleTestCase):
    def test_risk_domain_modules_are_importable(self):
        modules = [
            'risk',
            'risk.selectors',
            'risk.services',
            'risk.services.affordability',
            'risk.services.second_credit',
            'risk.services.portfolio_takeover',
            'risk.services.policy_engine',
            'risk.policies',
        ]

        for module in modules:
            with self.subTest(module=module):
                import_module(module)

    def test_libranza_domain_modules_are_importable(self):
        modules = [
            'libranza',
            'libranza.selectors',
            'libranza.services',
            'libranza.services.legal_rules',
            'libranza.services.payment_capacity',
            'libranza.services.payer_validation',
            'libranza.services.payroll_law',
            'libranza.policies',
        ]

        for module in modules:
            with self.subTest(module=module):
                import_module(module)

