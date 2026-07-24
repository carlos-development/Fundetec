import hashlib
import json
import secrets
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from gestion_creditos.models import (
    Credito,
    Empresa,
    VinculoLaboralEmpresa,
    WhatsAppInternalAPIAuditLog,
    WhatsAppInternalApplication,
)
from gestion_creditos.services.credit_simulation import (
    PRODUCT_PAYROLL_LOAN,
    PRODUCT_WHATSAPP_CREDIT,
    SUPPORTED_PRODUCTS,
    calculate_credit_simulation,
    decimal_to_string,
    get_product_config,
)


TWOPLACES = Decimal('0.01')
WHATSAPP_CREDIT_MIN_AMOUNT = Decimal('300000.00')
WHATSAPP_CREDIT_MAX_AMOUNT = Decimal('2000000.00')
PAYROLL_LOAN_MIN_AMOUNT = Decimal('500000.00')
PAYROLL_LOAN_MAX_AMOUNT = Decimal('3000000.00')
MIN_TERM_MONTHS = 1
MAX_TERM_MONTHS = 6
IDEMPOTENCY_WINDOW_HOURS = 24
FLOW_MEDIA_KEYS = {'bank_certificate', 'id_front', 'id_back'}
FLOW_MEDIA_ALLOWED_FIELDS = {'media_id', 'filename', 'mime_type'}


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def normalize_document(value):
    return ''.join(ch for ch in str(value or '').strip() if ch.isalnum())


def normalize_phone(value):
    return ''.join(ch for ch in str(value or '').strip() if ch.isdigit())


def document_hash(document_number):
    normalized = normalize_document(document_number)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else ''


def mask_document(document_number):
    normalized = normalize_document(document_number)
    if len(normalized) <= 4:
        return '*' * len(normalized)
    return f"{'*' * (len(normalized) - 4)}{normalized[-4:]}"


def mask_phone(phone):
    normalized = normalize_phone(phone)
    if len(normalized) <= 4:
        return '*' * len(normalized)
    return f"{'*' * (len(normalized) - 4)}{normalized[-4:]}"


def safe_header_value(value, max_length=80):
    cleaned = ''.join(ch for ch in str(value or '').strip() if ch.isalnum() or ch in '-_:.')
    return cleaned[:max_length]


def build_request_observability_context(request):
    request_id = safe_header_value(request.headers.get('X-Request-ID')) or uuid.uuid4().hex
    correlation_id = safe_header_value(request.headers.get('X-Correlation-ID')) or request_id
    idempotency_key = safe_header_value(request.headers.get('X-Idempotency-Key'), max_length=160)
    context = {
        'request_id': request_id,
        'correlation_id': correlation_id,
        'idempotency_key_hash': hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest() if idempotency_key else '',
    }
    request.whatsapp_internal_observability = context
    return context


def parse_decimal(value, field_name, errors, *, minimum=None):
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        errors[field_name] = 'Debe ser un numero valido.'
        return None
    if minimum is not None and decimal_value < minimum:
        errors[field_name] = f'Debe ser mayor o igual a {minimum}.'
        return None
    return decimal_value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def parse_positive_int(value, field_name, errors):
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        errors[field_name] = 'Debe ser un entero valido.'
        return None
    if int_value <= 0:
        errors[field_name] = 'Debe ser mayor que cero.'
        return None
    return int_value


def get_products_payload(product_type=None):
    try:
        products = [get_product_config(PRODUCT_PAYROLL_LOAN), get_product_config(PRODUCT_WHATSAPP_CREDIT)]
        if product_type:
            products = [get_product_config(product_type)]
    except ValueError:
        raise ValidationError({'product_type': 'Producto no soportado.'})
    return [
        {
            'product_type': item.product_type,
            'name': item.name,
            'description': item.description,
            'current_flow': item.current_flow,
            'monthly_rate': format(item.monthly_rate, 'f'),
            'origination_rate': format(item.origination_rate, 'f'),
            'vat_rate': format(item.vat_rate, 'f'),
        }
        for item in products
    ]


def simulate_credit(payload):
    errors = {}
    product_type = payload.get('product_type')
    if product_type not in SUPPORTED_PRODUCTS:
        errors['product_type'] = 'Use payroll_loan o whatsapp_credit.'

    amount = parse_decimal(payload.get('amount'), 'amount', errors, minimum=Decimal('1.00'))
    term_months = parse_positive_int(payload.get('term_months'), 'term_months', errors)
    phone = normalize_phone(payload.get('phone'))
    if not phone:
        errors['phone'] = 'Este campo es obligatorio.'

    if errors:
        raise ValidationError(errors)

    _validate_simulation_limits(product_type, amount, term_months)
    return calculate_credit_simulation(
        product_type=product_type,
        amount=amount,
        term_months=term_months,
        document_number=payload.get('document_number'),
    )


def _validate_simulation_limits(product_type, amount, term_months):
    errors = {}
    if product_type == PRODUCT_WHATSAPP_CREDIT:
        if amount < WHATSAPP_CREDIT_MIN_AMOUNT:
            errors['amount'] = 'El monto minimo para whatsapp_credit es 300000.'
        if amount > WHATSAPP_CREDIT_MAX_AMOUNT:
            errors['amount'] = 'El monto maximo para whatsapp_credit es 2000000.'
        if term_months < MIN_TERM_MONTHS or term_months > MAX_TERM_MONTHS:
            errors['term_months'] = 'El plazo maximo para whatsapp_credit es 6 meses.'
    elif product_type == PRODUCT_PAYROLL_LOAN:
        if amount < PAYROLL_LOAN_MIN_AMOUNT:
            errors['amount'] = 'El monto minimo para payroll_loan es 500000.'
        if amount > PAYROLL_LOAN_MAX_AMOUNT:
            errors['amount'] = 'El monto maximo para payroll_loan es 3000000.'
        if term_months < MIN_TERM_MONTHS or term_months > MAX_TERM_MONTHS:
            errors['term_months'] = 'El plazo maximo para payroll_loan es 6 meses.'
    if errors:
        raise ValidationError(errors)


def create_whatsapp_credit_application(payload):
    data = _validate_base_application_payload(payload)
    product_type = payload.get('product_type') or PRODUCT_WHATSAPP_CREDIT
    if product_type not in SUPPORTED_PRODUCTS:
        raise ValidationError({'product_type': 'Use whatsapp_credit o payroll_loan.'})
    if product_type != PRODUCT_WHATSAPP_CREDIT:
        raise ValidationError({'product_type': 'Use el endpoint separado de libranza para payroll_loan.'})
    _validate_whatsapp_credit_limits(data)
    existing = _find_idempotent_application(PRODUCT_WHATSAPP_CREDIT, data, payload)
    if existing:
        return _application_created_payload(
            existing,
            next_step='risk_prevalidation',
            message='Solicitud recibida para validacion inicial del credito por WhatsApp.',
            idempotent_replay=True,
        )

    application = WhatsAppInternalApplication.objects.create(
        product_type=PRODUCT_WHATSAPP_CREDIT,
        status=WhatsAppInternalApplication.Status.RECEIVED,
        **data,
    )
    return _application_created_payload(
        application,
        next_step='risk_prevalidation',
        message='Solicitud recibida para validacion inicial del credito por WhatsApp.',
    )


def create_payroll_application(payload):
    data = _validate_base_application_payload(payload)
    product_type = payload.get('product_type')
    if product_type not in SUPPORTED_PRODUCTS:
        raise ValidationError({'product_type': 'Use whatsapp_credit o payroll_loan.'})
    if product_type != PRODUCT_PAYROLL_LOAN:
        raise ValidationError({'product_type': 'Use payroll_loan en este endpoint.'})
    _validate_payroll_loan_limits(data)

    empresa_id = payload.get('empresa_id')
    empresa_nombre = (payload.get('empresa_nombre') or '').strip()
    errors = {}
    if not empresa_id and not empresa_nombre:
        errors['empresa'] = 'Debe enviar empresa_id o empresa_nombre.'
    if errors:
        raise ValidationError(errors)

    empresa, payroll_validation = _build_payroll_validation(
        empresa_id=empresa_id,
        empresa_nombre=empresa_nombre,
        document_number=data['numero_documento'],
    )
    vinculo_validado = False
    if payroll_validation['empresa_convenio_activo'] and payroll_validation['empresa_tipo_valido']:
        vinculo_validado = payroll_validation['vinculo_laboral_validado']
    status = (
        WhatsAppInternalApplication.Status.PENDING_FORM_COMPLETION
        if payroll_validation['ready_for_existing_flow']
        else WhatsAppInternalApplication.Status.PENDING_PAYROLL_VALIDATION
    )
    data['metadata']['payroll_validation'] = payroll_validation
    existing = _find_idempotent_application(PRODUCT_PAYROLL_LOAN, data, payload)
    if existing:
        next_step = (
            'continue_existing_libranza_flow'
            if existing.status == WhatsAppInternalApplication.Status.PENDING_FORM_COMPLETION
            else 'pending_payroll_validation'
        )
        message = (
            'Solicitud de libranza iniciada. Debe continuar el flujo existente de formulario, documentos y pagare.'
            if existing.status == WhatsAppInternalApplication.Status.PENDING_FORM_COMPLETION
            else 'Solicitud de libranza recibida para validacion de convenio y vinculo laboral.'
        )
        return _application_created_payload(existing, next_step=next_step, message=message, idempotent_replay=True)

    application = WhatsAppInternalApplication.objects.create(
        product_type=PRODUCT_PAYROLL_LOAN,
        status=status,
        empresa=empresa,
        convenio_validado=payroll_validation['empresa_convenio_activo'] and payroll_validation['empresa_tipo_valido'],
        vinculo_laboral_validado=vinculo_validado,
        **data,
    )
    next_step = (
        'continue_existing_libranza_flow'
        if payroll_validation['ready_for_existing_flow']
        else 'pending_payroll_validation'
    )
    message = (
        'Solicitud de libranza iniciada. Debe continuar el flujo existente de formulario, documentos y pagare.'
        if payroll_validation['ready_for_existing_flow']
        else 'Solicitud de libranza recibida para validacion de convenio y vinculo laboral.'
    )
    return _application_created_payload(application, next_step=next_step, message=message)


def _application_created_payload(application, *, next_step, message, idempotent_replay=False):
    payload = {
        'application_id': application.id,
        'status': application.status,
        'next_step': next_step,
        'message': message,
    }
    if idempotent_replay:
        payload['idempotent_replay'] = True
    return payload


def _build_payroll_validation(*, empresa_id, empresa_nombre, document_number):
    empresa = None
    if empresa_id:
        empresa = Empresa.objects.filter(id=empresa_id).first()
    elif empresa_nombre:
        empresa = Empresa.objects.filter(nombre__iexact=empresa_nombre).first()

    validation = {
        'empresa_found': bool(empresa),
        'empresa_convenio_activo': bool(empresa and empresa.convenio_activo),
        'empresa_tipo_valido': bool(
            empresa and empresa.tipo_empresa in [Empresa.TipoEmpresa.CONVENIO, Empresa.TipoEmpresa.MIXTA]
        ),
        'vinculo_laboral_validado': False,
        'ready_for_existing_flow': False,
        'pending_reasons': [],
    }

    if not empresa:
        validation['pending_reasons'].append('empresa_no_encontrada')
        return None, validation

    if not validation['empresa_convenio_activo']:
        validation['pending_reasons'].append('empresa_sin_convenio_activo')
    if not validation['empresa_tipo_valido']:
        validation['pending_reasons'].append('empresa_tipo_no_valido')

    if validation['empresa_convenio_activo'] and validation['empresa_tipo_valido']:
        validation['vinculo_laboral_validado'] = VinculoLaboralEmpresa.objects.filter(
            empresa=empresa,
            documento_empleado=document_number,
            estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            validado_por_pagador=True,
        ).exists()
        if not validation['vinculo_laboral_validado']:
            validation['pending_reasons'].append('vinculo_laboral_no_validado')

    validation['ready_for_existing_flow'] = (
        validation['empresa_convenio_activo']
        and validation['empresa_tipo_valido']
        and validation['vinculo_laboral_validado']
    )
    return empresa, validation


def _validate_base_application_payload(payload):
    required = [
        'tipo_documento',
        'numero_documento',
        'nombres',
        'apellidos',
        'celular',
        'correo',
        'direccion',
        'ingresos_mensuales',
        'monto_solicitado',
        'plazo_meses',
        'autorizacion_tratamiento_datos',
        'autorizacion_validacion_informacion',
    ]
    errors = {}
    for field in required:
        if payload.get(field) in (None, ''):
            errors[field] = 'Este campo es obligatorio.'

    document_number = normalize_document(payload.get('numero_documento'))
    phone = normalize_phone(payload.get('celular'))
    amount = parse_decimal(payload.get('monto_solicitado'), 'monto_solicitado', errors, minimum=Decimal('1.00'))
    income = parse_decimal(payload.get('ingresos_mensuales'), 'ingresos_mensuales', errors, minimum=Decimal('0.00'))
    term = parse_positive_int(payload.get('plazo_meses'), 'plazo_meses', errors)
    media_metadata = _validate_media_metadata(payload.get('media_metadata'), errors)

    if not document_number:
        errors['numero_documento'] = 'Este campo es obligatorio.'
    if not phone:
        errors['celular'] = 'Este campo es obligatorio.'
    if payload.get('source') != 'whatsapp':
        errors['source'] = 'Debe ser whatsapp.'
    if payload.get('autorizacion_tratamiento_datos') is not True:
        errors['autorizacion_tratamiento_datos'] = 'Debe ser true.'
    if payload.get('autorizacion_validacion_informacion') is not True:
        errors['autorizacion_validacion_informacion'] = 'Debe ser true.'

    if errors:
        raise ValidationError(errors)

    return {
        'source': 'whatsapp',
        'tipo_documento': str(payload.get('tipo_documento')).strip(),
        'numero_documento': document_number,
        'nombres': str(payload.get('nombres')).strip(),
        'apellidos': str(payload.get('apellidos')).strip(),
        'celular': phone,
        'correo': str(payload.get('correo')).strip(),
        'ciudad': str(payload.get('ciudad') or '').strip(),
        'ocupacion': str(payload.get('ocupacion') or '').strip(),
        'ingresos_mensuales': income,
        'monto_solicitado': amount,
        'plazo_meses': term,
        'autorizacion_tratamiento_datos': True,
        'autorizacion_validacion_informacion': True,
        'metadata': {
            'source_payload_version': 'whatsapp_flow_v1',
            'direccion': str(payload.get('direccion')).strip(),
            'media_metadata': media_metadata,
            'media_processing': 'pending_not_downloaded',
        },
    }


def _validate_whatsapp_credit_limits(data):
    errors = {}
    if data['monto_solicitado'] < WHATSAPP_CREDIT_MIN_AMOUNT:
        errors['monto_solicitado'] = 'El monto minimo para whatsapp_credit es 300000.'
    if data['monto_solicitado'] > WHATSAPP_CREDIT_MAX_AMOUNT:
        errors['monto_solicitado'] = 'El monto maximo para whatsapp_credit es 2000000.'
    if data['plazo_meses'] < MIN_TERM_MONTHS or data['plazo_meses'] > MAX_TERM_MONTHS:
        errors['plazo_meses'] = 'El plazo maximo para whatsapp_credit es 6 meses.'
    if errors:
        raise ValidationError(errors)


def _validate_payroll_loan_limits(data):
    errors = {}
    if data['monto_solicitado'] < PAYROLL_LOAN_MIN_AMOUNT:
        errors['monto_solicitado'] = 'El monto minimo para payroll_loan es 500000.'
    if data['monto_solicitado'] > PAYROLL_LOAN_MAX_AMOUNT:
        errors['monto_solicitado'] = 'El monto maximo para payroll_loan es 3000000.'
    if data['plazo_meses'] < MIN_TERM_MONTHS or data['plazo_meses'] > MAX_TERM_MONTHS:
        errors['plazo_meses'] = 'El plazo maximo para payroll_loan es 6 meses.'
    if errors:
        raise ValidationError(errors)


def _find_idempotent_application(product_type, data, payload):
    fingerprint = _payload_fingerprint(product_type, data, payload)
    data['metadata']['payload_fingerprint'] = fingerprint
    cutoff = timezone.now() - timezone.timedelta(
        hours=int(getattr(settings, 'WHATSAPP_INTERNAL_IDEMPOTENCY_WINDOW_HOURS', IDEMPOTENCY_WINDOW_HOURS))
    )
    return WhatsAppInternalApplication.objects.filter(
        product_type=product_type,
        source='whatsapp',
        numero_documento=data['numero_documento'],
        monto_solicitado=data['monto_solicitado'],
        plazo_meses=data['plazo_meses'],
        metadata__payload_fingerprint=fingerprint,
        created_at__gte=cutoff,
    ).order_by('-created_at').first()


def _payload_fingerprint(product_type, data, payload):
    media_metadata = data.get('metadata', {}).get('media_metadata') or {}
    media_ids = {
        key: (value or {}).get('media_id', '')
        for key, value in sorted(media_metadata.items())
    }
    raw = {
        'product_type': product_type,
        'source': 'whatsapp',
        'numero_documento_hash': document_hash(data['numero_documento']),
        'celular_tail': data['celular'][-4:],
        'monto_solicitado': decimal_to_string(data['monto_solicitado']),
        'plazo_meses': data['plazo_meses'],
        'empresa_id': str(payload.get('empresa_id') or ''),
        'empresa_nombre': str(payload.get('empresa_nombre') or '').strip().lower(),
        'media_ids': media_ids,
    }
    serialized = json.dumps(raw, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _validate_media_metadata(media_metadata, errors):
    if media_metadata in (None, ''):
        return {}
    if not isinstance(media_metadata, dict):
        errors['media_metadata'] = 'Debe ser un objeto JSON.'
        return {}

    unknown_keys = sorted(set(media_metadata) - FLOW_MEDIA_KEYS)
    if unknown_keys:
        errors['media_metadata'] = f"Campos no soportados: {', '.join(unknown_keys)}."
        return {}

    normalized = {}
    received_at = timezone.now().isoformat()
    for key, value in media_metadata.items():
        if value in (None, ''):
            continue
        if not isinstance(value, dict):
            errors[f'media_metadata.{key}'] = 'Debe ser un objeto JSON.'
            continue
        if not value.get('media_id'):
            errors[f'media_metadata.{key}.media_id'] = 'Este campo es obligatorio.'
            continue
        normalized[key] = {
            str(item_key): str(item_value)
            for item_key, item_value in value.items()
            if item_key in FLOW_MEDIA_ALLOWED_FIELDS and item_value not in (None, '')
        }
        normalized[key]['field_name'] = key
        normalized[key]['received_at'] = received_at
    return normalized


def get_application_status(document_number, product_type=None):
    document_number = normalize_document(document_number)
    if not document_number:
        raise ValidationError({'document_number': 'Este campo es obligatorio.'})
    if product_type and product_type not in SUPPORTED_PRODUCTS:
        raise ValidationError({'product_type': 'Use payroll_loan o whatsapp_credit.'})

    if product_type == PRODUCT_PAYROLL_LOAN:
        credito = _latest_payroll_credit(document_number)
        if credito:
            return _credit_application_payload(credito)

    if product_type in (None, PRODUCT_WHATSAPP_CREDIT):
        application = WhatsAppInternalApplication.objects.filter(
            numero_documento=document_number,
            product_type=PRODUCT_WHATSAPP_CREDIT,
        ).order_by('-created_at').first()
        if application:
            return _internal_application_payload(application)

    if product_type is None:
        credito = _latest_payroll_credit(document_number)
        if credito:
            return _credit_application_payload(credito)

    return None


def get_credit_status(document_number, product_type=None):
    document_number = normalize_document(document_number)
    if not document_number:
        raise ValidationError({'document_number': 'Este campo es obligatorio.'})
    if product_type and product_type not in SUPPORTED_PRODUCTS:
        raise ValidationError({'product_type': 'Use payroll_loan o whatsapp_credit.'})

    if product_type == PRODUCT_WHATSAPP_CREDIT:
        return None

    credito = _latest_payroll_credit(
        document_number,
        states=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA],
    )
    if not credito:
        return None

    return {
        'has_active_credit': True,
        'product_type': PRODUCT_PAYROLL_LOAN,
        'credit_reference': credito.numero_credito,
        'status': credito.estado,
        'status_label': credito.get_estado_display(),
        'next_payment_date': credito.fecha_proximo_pago.isoformat() if credito.fecha_proximo_pago else None,
        'days_past_due': credito.dias_en_mora,
    }


def list_documents(document_number, product_type=None):
    document_number = normalize_document(document_number)
    if not document_number:
        raise ValidationError({'document_number': 'Este campo es obligatorio.'})
    if product_type and product_type not in SUPPORTED_PRODUCTS:
        raise ValidationError({'product_type': 'Use payroll_loan o whatsapp_credit.'})

    documents = []
    if product_type in (None, PRODUCT_PAYROLL_LOAN):
        credito = _latest_payroll_credit(document_number)
        detalle = getattr(credito, 'detalle_libranza', None) if credito else None
        if detalle:
            field_map = [
                ('cedula_frontal', 'Cedula frontal'),
                ('cedula_trasera', 'Cedula trasera'),
                ('certificado_laboral', 'Certificado laboral'),
                ('desprendible_nomina', 'Desprendible de nomina'),
                ('certificado_bancario', 'Certificado bancario'),
            ]
            for field_name, label in field_map:
                file_field = getattr(detalle, field_name, None)
                documents.append({
                    'product_type': PRODUCT_PAYROLL_LOAN,
                    'document_type': field_name,
                    'label': label,
                    'available': bool(file_field),
                    'delivery': 'not_available_without_strong_identity_validation',
                })
            documents.append({
                'product_type': PRODUCT_PAYROLL_LOAN,
                'document_type': 'pagare',
                'label': 'Pagare',
                'available': hasattr(credito, 'pagare'),
                'delivery': 'not_available_without_strong_identity_validation',
            })

    if product_type in (None, PRODUCT_WHATSAPP_CREDIT):
        application = WhatsAppInternalApplication.objects.filter(
            numero_documento=document_number,
            product_type=PRODUCT_WHATSAPP_CREDIT,
        ).first()
        if application:
            documents.append({
                'product_type': PRODUCT_WHATSAPP_CREDIT,
                'document_type': 'initial_application',
                'label': 'Solicitud inicial',
                'available': True,
                'delivery': 'not_available_without_strong_identity_validation',
            })

    return {'documents': documents}


def validate_identity(payload):
    errors = {}
    document_number = normalize_document(payload.get('document_number'))
    phone = normalize_phone(payload.get('phone'))
    if not document_number:
        errors['document_number'] = 'Este campo es obligatorio.'
    if not phone:
        errors['phone'] = 'Este campo es obligatorio.'
    elif len(phone) < 7:
        errors['phone'] = 'Debe incluir al menos 7 digitos.'
    if errors:
        raise ValidationError(errors)

    phone_matches = Q(detalle_libranza__telefono__contains=phone[-7:])
    has_credit_match = Credito.objects.filter(detalle_libranza__cedula=document_number).filter(phone_matches).exists()
    has_application_match = WhatsAppInternalApplication.objects.filter(
        numero_documento=document_number,
        celular__contains=phone[-7:] if len(phone) >= 7 else phone,
    ).exists()
    validated = has_credit_match or has_application_match
    token = None
    expires_in = 0
    if validated:
        token = secrets.token_urlsafe(32)
        expires_in = int(getattr(settings, 'WHATSAPP_INTERNAL_IDENTITY_TOKEN_SECONDS', 600))
        cache.set(
            f"whatsapp-internal-identity:{token}",
            {'document_hash': document_hash(document_number), 'phone_tail': phone[-4:]},
            timeout=expires_in,
        )

    return {
        'identity_validated': validated,
        'identity_token': token,
        'expires_in_seconds': expires_in,
    }


def _latest_payroll_credit(document_number, states=None):
    queryset = Credito.objects.filter(
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__cedula=document_number,
    ).select_related('detalle_libranza')
    if states:
        queryset = queryset.filter(estado__in=states)
    return queryset.order_by('-fecha_solicitud').first()


def _credit_application_payload(credito):
    return {
        'application_id': credito.id,
        'product_type': PRODUCT_PAYROLL_LOAN,
        'status': credito.estado,
        'status_label': credito.get_estado_display(),
        'source': 'aprobado_backend',
        'created_at': credito.fecha_solicitud.isoformat() if credito.fecha_solicitud else None,
        'next_step': _next_step_for_credit(credito),
    }


def _internal_application_payload(application):
    return {
        'application_id': application.id,
        'product_type': application.product_type,
        'status': application.status,
        'status_label': application.get_status_display(),
        'source': application.source,
        'created_at': application.created_at.isoformat(),
        'next_step': 'risk_prevalidation' if application.product_type == PRODUCT_WHATSAPP_CREDIT else 'continue_existing_libranza_flow',
    }


def _next_step_for_credit(credito):
    if credito.estado == Credito.EstadoCredito.SOLICITUD:
        return 'internal_review'
    if credito.estado == Credito.EstadoCredito.APROBADO_PAGADOR:
        return 'aprobado_review'
    if credito.estado == Credito.EstadoCredito.PENDIENTE_FIRMA:
        return 'sign_promissory_note'
    if credito.estado == Credito.EstadoCredito.ACTIVO:
        return 'credit_active'
    if credito.estado == Credito.EstadoCredito.RECHAZADO:
        return 'closed'
    return 'continue_existing_libranza_flow'


def audit_request(request, *, action, status_code, product_type='', document_number='', phone='', metadata=None):
    context = getattr(request, 'whatsapp_internal_observability', None) or build_request_observability_context(request)
    metadata = {
        **(metadata or {}),
        'correlation_id': context['correlation_id'],
        'request_id': context['request_id'],
    }
    if context.get('idempotency_key_hash'):
        metadata['idempotency_key_hash'] = context['idempotency_key_hash']
    WhatsAppInternalAPIAuditLog.objects.create(
        action=action,
        product_type=product_type or '',
        request_id=context['request_id'],
        correlation_id=context['correlation_id'],
        document_number_hash=document_hash(document_number),
        document_number_masked=mask_document(document_number),
        phone_masked=mask_phone(phone),
        method=request.method,
        path=request.path[:255],
        status_code=status_code,
        ip_address=get_client_ip(request),
        user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:255],
        metadata=metadata or {},
    )


def get_client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
