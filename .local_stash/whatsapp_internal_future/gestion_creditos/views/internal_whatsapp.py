import json
import logging
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from gestion_creditos.models import WhatsAppInternalConsent
from gestion_creditos.services.internal_whatsapp_api import (
    PRODUCT_PAYROLL_LOAN,
    ValidationError,
    audit_request,
    build_request_observability_context,
    create_payroll_application,
    create_whatsapp_credit_application,
    document_hash,
    get_application_status,
    get_client_ip,
    get_credit_status,
    get_products_payload,
    list_documents,
    normalize_document,
    normalize_phone,
    simulate_credit,
    validate_identity,
)

logger = logging.getLogger('gestion_creditos.internal_whatsapp')


def _json_error(message, status=400, errors=None):
    payload = {'error': message}
    if errors:
        payload['errors'] = errors
    return JsonResponse(payload, status=status)


def _validation_error_metadata(errors):
    return {
        'result': 'validation_error',
        'validation_errors': errors,
    }


def _parse_json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValidationError({'body': 'JSON invalido.'})


def _authenticated(request):
    configured_key = getattr(settings, 'WHATSAPP_INTERNAL_API_KEY', '')
    provided_key = request.headers.get('X-Internal-API-Key', '')
    return bool(configured_key) and secrets.compare_digest(provided_key, configured_key)


def _extract_product_type(request):
    if request.method == 'GET':
        return request.GET.get('product_type') or ''
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return ''
    return payload.get('product_type') or ''


def _result_from_response(response):
    status_code = getattr(response, 'status_code', 0)
    error_type = ''
    result = 'success'
    body = {}
    try:
        body = json.loads(response.content.decode('utf-8') or '{}')
    except Exception:
        body = {}

    if status_code >= 500:
        result = 'server_error'
        error_type = body.get('error') or 'server_error'
    elif status_code == 401:
        result = 'auth_error'
        error_type = 'auth'
    elif status_code == 404:
        result = 'not_found'
        error_type = 'not_found'
    elif status_code == 405:
        result = 'method_not_allowed'
        error_type = 'method_not_allowed'
    elif status_code == 429:
        result = 'rate_limited'
        error_type = 'rate_limit'
    elif status_code >= 400:
        result = 'validation_error' if body.get('errors') else 'client_error'
        error_type = 'validation' if body.get('errors') else (body.get('error') or 'client_error')
    elif body.get('idempotent_replay'):
        result = 'idempotent_replay'
    elif 'application_id' in body:
        result = 'created'
    elif body.get('identity_validated') is True:
        result = 'validated'
    elif body.get('identity_validated') is False:
        result = 'not_validated'
    return result, error_type


def _safe_validation_error_fields(response):
    try:
        body = json.loads(response.content.decode('utf-8') or '{}')
    except Exception:
        return []
    errors = body.get('errors') or {}
    if isinstance(errors, dict):
        return sorted(str(key) for key in errors.keys())
    return []


def _metric_add(key, amount=1):
    try:
        cache.add(key, 0, None)
        cache.incr(key, amount)
    except Exception:
        try:
            cache.set(key, (cache.get(key, 0) or 0) + amount, None)
        except Exception:
            logger.debug('whatsapp_internal_metric_write_failed', extra={'metric_key': key}, exc_info=True)


def _record_metrics(endpoint, status_code, latency_ms):
    base_key = f"whatsapp-internal-api:metrics:{endpoint}"
    _metric_add(f"{base_key}:total")
    if 200 <= status_code < 300:
        _metric_add(f"{base_key}:2xx")
    elif 400 <= status_code < 500:
        _metric_add(f"{base_key}:4xx")
    elif status_code >= 500:
        _metric_add(f"{base_key}:5xx")
    _metric_add(f"{base_key}:latency_ms_count")
    _metric_add(f"{base_key}:latency_ms_total", int(latency_ms))
    cache.set(f"{base_key}:latency_ms_last", int(latency_ms), None)


def _log_request(request, *, endpoint, product_type, status_code, latency_ms, result, error_type):
    context = getattr(request, 'whatsapp_internal_observability', {})
    log_payload = {
        'request_id': context.get('request_id', ''),
        'correlation_id': context.get('correlation_id', ''),
        'endpoint': endpoint,
        'method': request.method,
        'product_type': product_type or '',
        'status_code': status_code,
        'latency_ms': int(latency_ms),
        'result': result,
        'error_type': error_type or '',
    }
    if context.get('idempotency_key_hash'):
        log_payload['idempotency_key_hash'] = context['idempotency_key_hash']
    error_fields = _safe_validation_error_fields(getattr(request, 'whatsapp_internal_response', None)) if error_type == 'validation' else []
    if error_fields:
        log_payload['error_fields'] = error_fields
    logger.info(json.dumps(log_payload, sort_keys=True, separators=(',', ':')))


def _rate_limit_allowed(request):
    limit = int(getattr(settings, 'WHATSAPP_INTERNAL_API_RATE_LIMIT', 120))
    if limit <= 0:
        return True
    ip = get_client_ip(request) or 'anon'
    api_key_hash = document_hash(request.headers.get('X-Internal-API-Key', ''))
    cache_key = f"whatsapp-internal-api:rate:{api_key_hash}:{ip}"
    try:
        hits = cache.get(cache_key, 0)
        if hits >= limit:
            return False
        cache.set(cache_key, hits + 1, 60)
    except Exception:
        return True
    return True


@method_decorator(csrf_exempt, name='dispatch')
class InternalWhatsAppAPIView(View):
    allowed_methods = None
    audit_action = 'internal_whatsapp_api'

    def dispatch(self, request, *args, **kwargs):
        start = time.perf_counter()
        build_request_observability_context(request)
        product_type = _extract_product_type(request)
        endpoint = self.audit_action
        response = None
        try:
            if not _authenticated(request):
                audit_request(request, action=self.audit_action, status_code=401, product_type=product_type)
                response = _json_error('API key requerida o invalida.', status=401)
                return response
            if self.allowed_methods and request.method not in self.allowed_methods:
                audit_request(request, action=self.audit_action, status_code=405, product_type=product_type)
                response = _json_error('Metodo no permitido.', status=405)
                return response
            if not _rate_limit_allowed(request):
                audit_request(request, action=self.audit_action, status_code=429, product_type=product_type)
                response = _json_error('Rate limit excedido.', status=429)
                return response
            response = super().dispatch(request, *args, **kwargs)
            return response
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_metrics(endpoint, 500, latency_ms)
            _log_request(
                request,
                endpoint=endpoint,
                product_type=product_type,
                status_code=500,
                latency_ms=latency_ms,
                result='server_error',
                error_type=exc.__class__.__name__,
            )
            raise
        finally:
            if response is not None:
                request.whatsapp_internal_response = response
                latency_ms = (time.perf_counter() - start) * 1000
                status_code = response.status_code
                result, error_type = _result_from_response(response)
                _record_metrics(endpoint, status_code, latency_ms)
                _log_request(
                    request,
                    endpoint=endpoint,
                    product_type=product_type,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    result=result,
                    error_type=error_type,
                )


class ProductsView(InternalWhatsAppAPIView):
    allowed_methods = {'GET'}
    audit_action = 'products'

    def get(self, request):
        product_type = request.GET.get('product_type') or None
        try:
            payload = {'products': get_products_payload(product_type)}
        except ValidationError as exc:
            audit_request(request, action=self.audit_action, status_code=400, product_type=product_type)
            return _json_error('Datos invalidos.', errors=exc.errors)
        audit_request(request, action=self.audit_action, status_code=200, product_type=product_type)
        return JsonResponse(payload)


class SimulationsView(InternalWhatsAppAPIView):
    allowed_methods = {'POST'}
    audit_action = 'simulation'

    def post(self, request):
        try:
            payload = _parse_json(request)
            result = simulate_credit(payload)
        except ValidationError as exc:
            audit_request(
                request,
                action=self.audit_action,
                status_code=400,
                product_type=(locals().get('payload') or {}).get('product_type', ''),
                document_number=(locals().get('payload') or {}).get('document_number', ''),
                metadata=_validation_error_metadata(exc.errors),
            )
            return _json_error('Datos invalidos.', errors=exc.errors)

        audit_request(
            request,
            action=self.audit_action,
            status_code=200,
            product_type=payload.get('product_type', ''),
            document_number=payload.get('document_number', ''),
        )
        return JsonResponse(result)


class ApplicationsView(InternalWhatsAppAPIView):
    allowed_methods = {'POST'}
    audit_action = 'application_create'

    def post(self, request):
        try:
            payload = _parse_json(request)
            result = create_whatsapp_credit_application(payload)
        except ValidationError as exc:
            audit_request(
                request,
                action=self.audit_action,
                status_code=400,
                product_type=(locals().get('payload') or {}).get('product_type', ''),
                document_number=(locals().get('payload') or {}).get('numero_documento', ''),
                metadata=_validation_error_metadata(exc.errors),
            )
            return _json_error('Datos invalidos.', errors=exc.errors)

        status_code = 200 if result.get('idempotent_replay') else 201
        audit_request(
            request,
            action=self.audit_action,
            status_code=status_code,
            product_type='whatsapp_credit',
            document_number=payload.get('numero_documento', ''),
            metadata={
                'result': 'idempotent_replay' if result.get('idempotent_replay') else 'created',
                'application_id': result['application_id'],
            },
        )
        return JsonResponse(result, status=status_code)


class PayrollApplicationsView(InternalWhatsAppAPIView):
    allowed_methods = {'POST'}
    audit_action = 'payroll_application_create'

    def post(self, request):
        try:
            payload = _parse_json(request)
            result = create_payroll_application(payload)
        except ValidationError as exc:
            audit_request(
                request,
                action=self.audit_action,
                status_code=400,
                product_type=(locals().get('payload') or {}).get('product_type', PRODUCT_PAYROLL_LOAN),
                document_number=(locals().get('payload') or {}).get('numero_documento', ''),
                metadata=_validation_error_metadata(exc.errors),
            )
            return _json_error('Datos invalidos.', errors=exc.errors)

        status_code = 200 if result.get('idempotent_replay') else 201
        audit_request(
            request,
            action=self.audit_action,
            status_code=status_code,
            product_type=PRODUCT_PAYROLL_LOAN,
            document_number=payload.get('numero_documento', ''),
            metadata={
                'result': 'idempotent_replay' if result.get('idempotent_replay') else 'created',
                'application_id': result['application_id'],
                'status': result['status'],
            },
        )
        return JsonResponse(result, status=status_code)


class ApplicationStatusView(InternalWhatsAppAPIView):
    allowed_methods = {'GET'}
    audit_action = 'application_status'

    def get(self, request):
        document_number = request.GET.get('document_number', '')
        product_type = request.GET.get('product_type') or None
        try:
            result = get_application_status(document_number, product_type)
        except ValidationError as exc:
            audit_request(request, action=self.audit_action, status_code=400, product_type=product_type or '', document_number=document_number)
            return _json_error('Datos invalidos.', errors=exc.errors)
        if not result:
            audit_request(request, action=self.audit_action, status_code=404, product_type=product_type or '', document_number=document_number)
            return _json_error('Solicitud no encontrada.', status=404)
        audit_request(request, action=self.audit_action, status_code=200, product_type=result.get('product_type', ''), document_number=document_number)
        return JsonResponse(result)


class CreditStatusView(InternalWhatsAppAPIView):
    allowed_methods = {'GET'}
    audit_action = 'credit_status'

    def get(self, request):
        document_number = request.GET.get('document_number', '')
        product_type = request.GET.get('product_type') or None
        try:
            result = get_credit_status(document_number, product_type)
        except ValidationError as exc:
            audit_request(request, action=self.audit_action, status_code=400, product_type=product_type or '', document_number=document_number)
            return _json_error('Datos invalidos.', errors=exc.errors)
        if not result:
            audit_request(request, action=self.audit_action, status_code=404, product_type=product_type or '', document_number=document_number)
            return _json_error('Credito activo no encontrado.', status=404)
        audit_request(request, action=self.audit_action, status_code=200, product_type=result.get('product_type', ''), document_number=document_number)
        return JsonResponse(result)


class DocumentsView(InternalWhatsAppAPIView):
    allowed_methods = {'GET'}
    audit_action = 'documents_list'

    def get(self, request):
        document_number = request.GET.get('document_number', '')
        product_type = request.GET.get('product_type') or None
        try:
            result = list_documents(document_number, product_type)
        except ValidationError as exc:
            audit_request(request, action=self.audit_action, status_code=400, product_type=product_type or '', document_number=document_number)
            return _json_error('Datos invalidos.', errors=exc.errors)
        audit_request(request, action=self.audit_action, status_code=200, product_type=product_type or '', document_number=document_number)
        return JsonResponse(result)


class IdentityValidateView(InternalWhatsAppAPIView):
    allowed_methods = {'POST'}
    audit_action = 'identity_validate'

    def post(self, request):
        try:
            payload = _parse_json(request)
            result = validate_identity(payload)
        except ValidationError as exc:
            audit_request(
                request,
                action=self.audit_action,
                status_code=400,
                document_number=(locals().get('payload') or {}).get('document_number', ''),
                phone=(locals().get('payload') or {}).get('phone', ''),
                metadata=_validation_error_metadata(exc.errors),
            )
            return _json_error('Datos invalidos.', errors=exc.errors)
        audit_request(
            request,
            action=self.audit_action,
            status_code=200,
            document_number=payload.get('document_number', ''),
            phone=payload.get('phone', ''),
            metadata={
                'result': 'validated' if result['identity_validated'] else 'not_validated',
                'identity_validated': result['identity_validated'],
                'expires_in_seconds': result['expires_in_seconds'],
            },
        )
        return JsonResponse(result)


class ConsentsView(InternalWhatsAppAPIView):
    allowed_methods = {'POST'}
    audit_action = 'consent_create'

    def post(self, request):
        try:
            payload = _parse_json(request)
            result = self._create_consent(payload, request)
        except ValidationError as exc:
            audit_request(
                request,
                action=self.audit_action,
                status_code=400,
                product_type=(locals().get('payload') or {}).get('product_type', ''),
                document_number=(locals().get('payload') or {}).get('document_number', ''),
            )
            return _json_error('Datos invalidos.', errors=exc.errors)

        audit_request(
            request,
            action=self.audit_action,
            status_code=201,
            product_type=payload.get('product_type', ''),
            document_number=payload.get('document_number', ''),
            metadata={'consent_id': result['consent_id']},
        )
        return JsonResponse(result, status=201)

    def _create_consent(self, payload, request):
        errors = {}
        product_type = payload.get('product_type')
        document_number = normalize_document(payload.get('document_number'))
        phone = normalize_phone(payload.get('phone'))
        consent_type = str(payload.get('consent_type') or '').strip()
        accepted = payload.get('accepted')
        if product_type not in {'payroll_loan', 'whatsapp_credit'}:
            errors['product_type'] = 'Use payroll_loan o whatsapp_credit.'
        if not document_number:
            errors['document_number'] = 'Este campo es obligatorio.'
        if not consent_type:
            errors['consent_type'] = 'Este campo es obligatorio.'
        if accepted is not True:
            errors['accepted'] = 'Debe ser true.'
        if errors:
            raise ValidationError(errors)

        consent = WhatsAppInternalConsent.objects.create(
            product_type=product_type,
            source=payload.get('source') or 'whatsapp',
            document_number=document_number,
            phone=phone,
            consent_type=consent_type,
            accepted=True,
            ip_address=get_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:255],
            evidence={
                'channel': 'whatsapp',
                'document_hash': document_hash(document_number),
                'text_version': payload.get('text_version') or '',
            },
        )
        return {
            'consent_id': consent.id,
            'status': 'registered',
            'message': 'Consentimiento registrado.',
        }
