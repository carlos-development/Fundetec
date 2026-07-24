from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

from gestion_creditos.models import Credito
from gestion_creditos.services.tasa_service import obtener_tasa_credito


PRODUCT_PAYROLL_LOAN = 'payroll_loan'
PRODUCT_WHATSAPP_CREDIT = 'whatsapp_credit'
SUPPORTED_PRODUCTS = {PRODUCT_PAYROLL_LOAN, PRODUCT_WHATSAPP_CREDIT}
TWOPLACES = Decimal('0.01')


@dataclass(frozen=True)
class ProductConfig:
    product_type: str
    name: str
    description: str
    current_flow: str
    monthly_rate: Decimal
    origination_rate: Decimal
    vat_rate: Decimal


def money(value):
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def decimal_to_string(value):
    return format(money(value), 'f')


def get_product_config(product_type):
    if product_type == PRODUCT_PAYROLL_LOAN:
        return ProductConfig(
            product_type=PRODUCT_PAYROLL_LOAN,
            name='Credito de libranza',
            description='Credito de libranza para empleados de empresas con convenio activo.',
            current_flow='aprobado.com.co/libranza/',
            monthly_rate=obtener_tasa_credito(Credito.LineaCredito.LIBRANZA),
            origination_rate=Decimal(str(getattr(settings, 'LIBRANZA_ORIGINATION_RATE', '10'))),
            vat_rate=Decimal(str(getattr(settings, 'LIBRANZA_VAT_RATE', '19'))),
        )
    if product_type == PRODUCT_WHATSAPP_CREDIT:
        return ProductConfig(
            product_type=PRODUCT_WHATSAPP_CREDIT,
            name='Credito por WhatsApp',
            description='Nueva linea de credito originada desde el bot de WhatsApp.',
            current_flow='whatsapp',
            monthly_rate=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_TASA_MENSUAL', '3.5'))),
            origination_rate=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_ORIGINATION_RATE', '10'))),
            vat_rate=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_VAT_RATE', '19'))),
        )
    raise ValueError('Producto no soportado.')


def calculate_credit_simulation(*, product_type, amount, term_months, document_number=None):
    """
    Fuente oficial backend para simulaciones usadas por el simulador web y API interna.

    Replica la formula del simulador publico: costo de originacion + IVA se financian
    como capital y la cuota se calcula por anualidad francesa con tasa mensual.
    """
    config = get_product_config(product_type)
    amount = money(amount)
    term_months = int(term_months)
    monthly_rate = config.monthly_rate / Decimal('100')
    origination_fee = money(amount * config.origination_rate / Decimal('100'))
    vat = money(origination_fee * config.vat_rate / Decimal('100'))
    principal_financed = money(amount + origination_fee + vat)

    if monthly_rate > 0:
        factor = (monthly_rate * (Decimal('1.00') + monthly_rate) ** term_months) / (
            ((Decimal('1.00') + monthly_rate) ** term_months) - Decimal('1.00')
        )
        monthly_payment = money(principal_financed * factor)
    else:
        monthly_payment = money(principal_financed / Decimal(term_months))

    total_to_pay = money(monthly_payment * Decimal(term_months))
    interest = money(max(Decimal('0.00'), total_to_pay - principal_financed))
    valid_until = timezone.localdate() + timezone.timedelta(
        days=int(getattr(settings, 'WHATSAPP_SIMULATION_VALID_DAYS', 7))
    )

    warnings = []
    if product_type == PRODUCT_PAYROLL_LOAN:
        warnings.append('La libranza requiere convenio activo del pagador y validacion laboral.')
    if not document_number:
        warnings.append('Sin numero de documento solo se genera una simulacion anonima.')

    return {
        'amount': decimal_to_string(amount),
        'term_months': term_months,
        'origination_fee': decimal_to_string(origination_fee),
        'vat': decimal_to_string(vat),
        'interest': decimal_to_string(interest),
        'total_to_pay': decimal_to_string(total_to_pay),
        'monthly_payment': decimal_to_string(monthly_payment),
        'valid_until': valid_until.isoformat(),
        'warnings': warnings,
    }
