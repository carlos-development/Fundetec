from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    Empresa,
    VinculoLaboralEmpresa,
    WhatsAppInternalApplication,
)


User = get_user_model()


WHATSAPP_DOCUMENT = '1001234567'
WHATSAPP_PHONE = '3001234567'
PAYROLL_DOCUMENT = '1007654321'
PAYROLL_PHONE = '3017654321'


def whatsapp_application_payload(**overrides):
    payload = {
        'product_type': 'whatsapp_credit',
        'tipo_documento': 'CC',
        'numero_documento': WHATSAPP_DOCUMENT,
        'nombres': 'Ana',
        'apellidos': 'Perez',
        'celular': WHATSAPP_PHONE,
        'correo': 'ana@example.com',
        'direccion': 'Calle 123 #45-67',
        'ciudad': 'Bogota',
        'ocupacion': 'Independiente',
        'ingresos_mensuales': '2500000',
        'monto_solicitado': '1000000',
        'plazo_meses': 6,
        'autorizacion_tratamiento_datos': True,
        'autorizacion_validacion_informacion': True,
        'source': 'whatsapp',
        'media_metadata': {
            'bank_certificate': {
                'media_id': 'wamid.bank.123',
                'filename': 'certificado.pdf',
                'mime_type': 'application/pdf',
            },
            'id_front': {
                'media_id': 'wamid.front.123',
                'filename': 'cedula-frontal.jpg',
                'mime_type': 'image/jpeg',
            },
            'id_back': {
                'media_id': 'wamid.back.123',
                'filename': 'cedula-trasera.jpg',
                'mime_type': 'image/jpeg',
            },
        },
    }
    payload.update(overrides)
    return payload


def payroll_application_payload(**overrides):
    payload = whatsapp_application_payload(
        product_type='payroll_loan',
        numero_documento=PAYROLL_DOCUMENT,
        celular=PAYROLL_PHONE,
        nombres='Luis',
        apellidos='Gomez',
        correo='luis@example.com',
        ocupacion='Empleado',
    )
    payload.update(overrides)
    return payload


def create_whatsapp_credit_fixture():
    return WhatsAppInternalApplication.objects.create(
        product_type=WhatsAppInternalApplication.ProductType.WHATSAPP_CREDIT,
        source='whatsapp',
        tipo_documento='CC',
        numero_documento=WHATSAPP_DOCUMENT,
        nombres='Ana',
        apellidos='Perez',
        celular=WHATSAPP_PHONE,
        correo='ana@example.com',
        ciudad='Bogota',
        ocupacion='Independiente',
        ingresos_mensuales=Decimal('2500000.00'),
        monto_solicitado=Decimal('1000000.00'),
        plazo_meses=6,
        autorizacion_tratamiento_datos=True,
        autorizacion_validacion_informacion=True,
    )


def create_payroll_loan_fixture():
    empresa = Empresa.objects.create(
        nombre='Empresa Convenio WhatsApp',
        convenio_activo=True,
        tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
    )
    user = User.objects.create_user(
        username='cliente-payroll-whatsapp',
        email='cliente.payroll@example.com',
    )
    VinculoLaboralEmpresa.objects.create(
        usuario=user,
        empresa=empresa,
        documento_empleado=PAYROLL_DOCUMENT,
        tipo_documento='CC',
        nombre_empleado='Luis Gomez',
        correo_empleado='luis@example.com',
        telefono_empleado=PAYROLL_PHONE,
        estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
        fecha_alta_aprobado=date(2026, 1, 1),
        salario_base_mensual=Decimal('2500000.00'),
        validado_por_pagador=True,
    )
    credito = Credito.objects.create(
        usuario=user,
        linea=Credito.LineaCredito.LIBRANZA,
        estado=Credito.EstadoCredito.ACTIVO,
        numero_credito='CR-2026-WA001',
        monto_solicitado=Decimal('1000000.00'),
        monto_aprobado=Decimal('1000000.00'),
        plazo_solicitado=6,
        plazo=6,
        valor_cuota=Decimal('190000.00'),
        saldo_pendiente=Decimal('900000.00'),
        capital_pendiente=Decimal('800000.00'),
        total_a_pagar=Decimal('1140000.00'),
        comision=Decimal('0.00'),
        iva_comision=Decimal('0.00'),
        fecha_proximo_pago=date(2026, 6, 1),
    )
    CreditoLibranza.objects.create(
        credito=credito,
        empresa=empresa,
        nombres='Luis',
        apellidos='Gomez',
        cedula=PAYROLL_DOCUMENT,
        direccion='Calle 1',
        telefono=PAYROLL_PHONE,
        correo_electronico='luis@example.com',
        certificado_bancario='credito_libranza/certificados_bancarios/certificado.pdf',
    )
    return {
        'empresa': empresa,
        'user': user,
        'credito': credito,
    }
