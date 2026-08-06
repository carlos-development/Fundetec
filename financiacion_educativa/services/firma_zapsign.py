import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoEventoWebhookFirmaEducativa,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    RolParticipante,
    TipoArtefactoContractualEducativo,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    EventoWebhookFirmaEducativa,
    ProcesoFirmaEducativa,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estados import transicionar_solicitud


MAX_SIGNED_PDF_BYTES = 20 * 1024 * 1024
MAX_UNSIGNED_PDF_BYTES = 10 * 1024 * 1024


class FirmaEducativaError(Exception):
    codigo = 'SIGNATURE_PROVIDER_ERROR'

    def __init__(self, mensaje, *, codigo=None):
        if codigo:
            self.codigo = str(codigo)[:60]
        super().__init__(mensaje)


class FirmaEducativaDeshabilitada(FirmaEducativaError):
    codigo = 'SIGNATURE_BACKEND_DISABLED'


class FirmaEducativaRespuestaInvalida(FirmaEducativaError):
    codigo = 'SIGNATURE_INVALID_RESPONSE'


class FirmaEducativaEnvioAmbiguo(FirmaEducativaError):
    codigo = 'SIGNATURE_SEND_AMBIGUOUS'


class FirmaEducativaRechazoPermanente(FirmaEducativaError):
    codigo = 'SIGNATURE_REQUEST_REJECTED'


@dataclass(frozen=True)
class ResultadoEnvioFirma:
    token_documento: str
    estado_proveedor: str


@dataclass(frozen=True)
class ResultadoWebhookFirma:
    estado: str
    codigo: str


class DisabledEducationalSignatureBackend:
    def enviar(self, **kwargs):
        raise FirmaEducativaDeshabilitada(
            'La integracion educativa de firma no esta habilitada.'
        )

    def descargar_firmado(self, **kwargs):
        raise FirmaEducativaDeshabilitada(
            'La integracion educativa de firma no esta habilitada.'
        )


class ZapSignEducationalSignatureBackend:
    def __init__(self):
        self.base_url = str(
            settings.FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL
        ).rstrip('/')
        self.api_token = str(
            settings.FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN
        ).strip()
        self.timeout = settings.FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS
        if not self.api_token:
            raise ImproperlyConfigured(
                'La credencial ZapSign educativa es obligatoria.'
            )

    @property
    def _headers(self):
        return {
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json',
        }

    def enviar(
        self,
        *,
        pdf,
        nombre_documento,
        external_id,
        firmante,
    ):
        import requests

        if not pdf.startswith(b'%PDF') or len(pdf) > MAX_UNSIGNED_PDF_BYTES:
            raise FirmaEducativaRespuestaInvalida(
                'El PDF contractual no cumple el formato permitido.'
            )
        tipo_documento = (
            'foreign_id'
            if firmante.tipo_documento
            in {
                TipoDocumentoIdentidad.CE,
                TipoDocumentoIdentidad.PASSPORT,
                TipoDocumentoIdentidad.OTHER,
            }
            else 'national_id'
        )
        payload = {
            'name': nombre_documento[:255],
            'base64_pdf': base64.b64encode(pdf).decode('ascii'),
            'external_id': external_id,
            'brand_name': 'Aprobado',
            'lang': 'es',
            'allow_refuse_signature': True,
            'signers': [
                {
                    'name': firmante.nombre_completo,
                    'email': firmante.correo,
                    'auth_mode': (
                        settings.FINANCIACION_EDUCATIVA_ZAPSIGN_AUTH_MODE
                    ),
                    'send_automatic_email': (
                        settings.FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL
                    ),
                    'send_automatic_whatsapp': False,
                    'require_selfie_photo': (
                        settings.FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE
                    ),
                    'lock_name': True,
                    'lock_email': True,
                },
            ],
        }
        if settings.FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE:
            payload['signers'][0]['selfie_validation_type'] = (
                settings.FINANCIACION_EDUCATIVA_ZAPSIGN_SELFIE_VALIDATION_TYPE
            )
        if tipo_documento == 'national_id':
            payload['signers'][0]['qualification'] = 'Deudor principal'
        try:
            respuesta = requests.post(
                f'{self.base_url}/docs/',
                json=payload,
                headers=self._headers,
                timeout=self.timeout,
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
        except requests.HTTPError as exc:
            estado_http = getattr(exc.response, 'status_code', 0) or 0
            if 400 <= estado_http < 500 and estado_http != 429:
                raise FirmaEducativaRechazoPermanente(
                    'ZapSign rechazo la configuracion del documento.',
                    codigo=f'SIGNATURE_HTTP_{estado_http}',
                ) from exc
            raise FirmaEducativaEnvioAmbiguo(
                'El envio requiere conciliacion antes de reintentarse.'
            ) from exc
        except requests.RequestException as exc:
            raise FirmaEducativaEnvioAmbiguo(
                'El envio requiere conciliacion antes de reintentarse.'
            ) from exc
        except ValueError as exc:
            raise FirmaEducativaEnvioAmbiguo(
                'La respuesta requiere conciliacion antes de reintentarse.'
            ) from exc
        token = str(datos.get('token') or '').strip()
        estado = str(datos.get('status') or 'pending').strip().lower()
        if not token or len(token) > 160:
            raise FirmaEducativaEnvioAmbiguo(
                'El proveedor no devolvio un identificador conciliable.'
            )
        return ResultadoEnvioFirma(
            token_documento=token,
            estado_proveedor=estado,
        )

    def descargar_firmado(self, *, token_documento):
        import requests

        try:
            detalle = requests.get(
                f'{self.base_url}/docs/{token_documento}/',
                headers=self._headers,
                timeout=self.timeout,
            )
            detalle.raise_for_status()
            datos = detalle.json()
        except (requests.RequestException, ValueError) as exc:
            raise FirmaEducativaError(
                'No fue posible consultar el documento firmado.'
            ) from exc
        if str(datos.get('status') or '').strip().lower() != 'signed':
            raise FirmaEducativaRespuestaInvalida(
                'El proveedor no confirma la firma completa.'
            )
        url = str(datos.get('signed_file') or '').strip()
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if (
            parsed.scheme != 'https'
            or not host
            or not (
                host.endswith('.zapsign.com.br')
                or host == 'zapsign.com.br'
                or host.endswith('.amazonaws.com')
            )
        ):
            raise FirmaEducativaRespuestaInvalida(
                'El proveedor devolvio una ubicacion de archivo no permitida.'
            )
        try:
            respuesta = requests.get(
                url,
                timeout=self.timeout,
                stream=True,
                allow_redirects=False,
            )
            respuesta.raise_for_status()
            try:
                partes = []
                total = 0
                for parte in respuesta.iter_content(chunk_size=64 * 1024):
                    if not parte:
                        continue
                    total += len(parte)
                    if total > MAX_SIGNED_PDF_BYTES:
                        raise FirmaEducativaRespuestaInvalida(
                            'El archivo firmado supera el tamano permitido.'
                        )
                    partes.append(parte)
                pdf = b''.join(partes)
            finally:
                respuesta.close()
        except FirmaEducativaRespuestaInvalida:
            raise
        except requests.RequestException as exc:
            raise FirmaEducativaError(
                'No fue posible descargar el documento firmado.'
            ) from exc
        if (
            not pdf.startswith(b'%PDF')
            or not pdf
        ):
            raise FirmaEducativaRespuestaInvalida(
                'El archivo firmado no es un PDF valido.'
            )
        return pdf


def _backend():
    backend_class = import_string(
        settings.FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND
    )
    return backend_class()


def _responsable_contractual(solicitud):
    responsables = list(
        solicitud.participantes.filter(
            responsable_contractual=True,
            roles__rol=RolParticipante.PRINCIPAL_DEBTOR,
        ).distinct()[:2]
    )
    if len(responsables) != 1 or not responsables[0].correo:
        raise ValidationError(
            'La firma requiere un responsable contractual con correo.'
        )
    return responsables[0]


def _hmac_destinatario(correo):
    clave = str(
        settings.FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY
    ).encode('utf-8')
    if not clave:
        raise ImproperlyConfigured(
            'La clave HMAC de destinatarios de firma es obligatoria.'
        )
    return hmac.new(
        clave,
        correo.strip().lower().encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


@transaction.atomic
def preparar_proceso_firma(*, artefacto):
    artefacto = ArtefactoContractualEducativo.objects.select_for_update().get(
        pk=artefacto.pk
    )
    if (
        artefacto.tipo
        != TipoArtefactoContractualEducativo.PROMISSORY_NOTE
        or not artefacto.vigente
    ):
        raise ValidationError('Solo un pagare vigente puede prepararse para firma.')
    responsable = _responsable_contractual(artefacto.solicitud)
    proceso, _ = ProcesoFirmaEducativa.objects.get_or_create(
        artefacto=artefacto,
        defaults={
            'solicitud': artefacto.solicitud,
            'external_id': f'edu-{artefacto.pk}',
            'destinatario_hmac': _hmac_destinatario(responsable.correo),
        },
    )
    return proceso


def _marcar_fallo_envio(proceso_id, codigo):
    with transaction.atomic():
        proceso = ProcesoFirmaEducativa.objects.select_for_update().get(
            pk=proceso_id
        )
        if proceso.estado == EstadoProcesoFirmaEducativa.SENDING:
            proceso.estado = EstadoProcesoFirmaEducativa.FAILED
            proceso.codigo_ultimo_error = codigo[:60]
            proceso.save(
                update_fields=[
                    'estado',
                    'codigo_ultimo_error',
                    'actualizado_en',
                ]
            )


def enviar_pagare_educativo(
    *,
    proceso,
    permitir_reintento_permanente=False,
):
    backend = _backend()
    ahora = timezone.now()
    with transaction.atomic():
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'artefacto',
            'solicitud',
        ).get(pk=proceso.pk)
        if proceso.estado in {
            EstadoProcesoFirmaEducativa.SENT,
            EstadoProcesoFirmaEducativa.SIGNED,
        }:
            return proceso
        if (
            proceso.estado == EstadoProcesoFirmaEducativa.FAILED
            and proceso.codigo_ultimo_error
            == FirmaEducativaEnvioAmbiguo.codigo
        ):
            raise ValidationError(
                'El envio ambiguo requiere conciliacion antes de reintentarse.'
            )
        if (
            proceso.estado == EstadoProcesoFirmaEducativa.FAILED
            and proceso.codigo_ultimo_error.startswith('SIGNATURE_HTTP_')
            and not permitir_reintento_permanente
        ):
            raise ValidationError(
                'El rechazo permanente requiere correccion y confirmacion operativa.'
            )
        if proceso.estado == EstadoProcesoFirmaEducativa.SENDING:
            limite = ahora - timedelta(
                seconds=settings.FINANCIACION_EDUCATIVA_ZAPSIGN_STALE_SECONDS
            )
            if proceso.envio_iniciado_en and proceso.envio_iniciado_en > limite:
                raise ValidationError('El envio del pagare ya esta en proceso.')
        if (
            proceso.intentos_envio
            >= settings.FINANCIACION_EDUCATIVA_ZAPSIGN_MAX_ATTEMPTS
        ):
            raise ValidationError('El proceso de firma agoto sus intentos de envio.')
        if (
            proceso.solicitud.estado
            != EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
        ):
            raise ValidationError('La solicitud no esta pendiente de pagare.')
        if (
            proceso.artefacto.estado
            != EstadoArtefactoContractualEducativo.GENERATED
            or not proceso.artefacto.vigente
        ):
            raise ValidationError('El pagare no esta disponible para envio.')
        proceso.estado = EstadoProcesoFirmaEducativa.SENDING
        proceso.intentos_envio += 1
        proceso.envio_iniciado_en = ahora
        proceso.codigo_ultimo_error = ''
        proceso.save(
            update_fields=[
                'estado',
                'intentos_envio',
                'envio_iniciado_en',
                'codigo_ultimo_error',
                'actualizado_en',
            ]
        )
        artefacto = proceso.artefacto
        firmante = _responsable_contractual(proceso.solicitud)

    try:
        with artefacto.archivo.open('rb') as archivo:
            pdf = archivo.read(MAX_UNSIGNED_PDF_BYTES + 1)
        resultado = backend.enviar(
            pdf=pdf,
            nombre_documento=f'Pagare educativo {artefacto.numero_documento}',
            external_id=proceso.external_id,
            firmante=firmante,
        )
    except FirmaEducativaError as exc:
        _marcar_fallo_envio(proceso.pk, exc.codigo)
        raise
    except Exception as exc:
        _marcar_fallo_envio(proceso.pk, 'SIGNATURE_UNEXPECTED_ERROR')
        raise FirmaEducativaError('Fallo inesperado al enviar el pagare.') from exc

    with transaction.atomic():
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'artefacto',
            'solicitud',
        ).get(pk=proceso.pk)
        if proceso.estado != EstadoProcesoFirmaEducativa.SENDING:
            raise ValidationError('El proceso de firma cambio durante el envio.')
        proceso.estado = EstadoProcesoFirmaEducativa.SENT
        proceso.token_documento_externo = resultado.token_documento
        proceso.codigo_ultimo_error = ''
        proceso.enviado_en = timezone.now()
        proceso.save(
            update_fields=[
                'estado',
                'token_documento_externo',
                'codigo_ultimo_error',
                'enviado_en',
                'actualizado_en',
            ]
        )
        artefacto = proceso.artefacto
        artefacto.estado = (
            EstadoArtefactoContractualEducativo.SENT_FOR_SIGNATURE
        )
        artefacto.full_clean()
        artefacto.save(update_fields=['estado', 'actualizado_en'])
        transicionar_solicitud(
            solicitud=proceso.solicitud,
            nuevo_estado=EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
            motivo='Pagare educativo enviado a firma.',
            metadata={
                'signature_process_id': str(proceso.pk),
                'provider': proceso.proveedor,
            },
        )
    return proceso


def _obtener_o_crear_evento(*, payload_hash, tipo_evento):
    try:
        with transaction.atomic():
            return EventoWebhookFirmaEducativa.objects.create(
                payload_hash=payload_hash,
                tipo_evento=tipo_evento[:50],
            ), True
    except IntegrityError:
        return EventoWebhookFirmaEducativa.objects.get(
            payload_hash=payload_hash
        ), False


def _marcar_evento(evento, *, estado, codigo, proceso=None):
    evento.estado = estado
    evento.codigo_resultado = codigo[:60]
    evento.procesado_en = timezone.now()
    evento.intentos += 1
    if proceso is not None:
        evento.proceso = proceso
    evento.save(
        update_fields=[
            'estado',
            'codigo_resultado',
            'procesado_en',
            'intentos',
            'proceso',
            'actualizado_en',
        ]
    )


def _finalizar_firma(*, evento, proceso, pdf_firmado):
    nombre_guardado = ''
    with transaction.atomic():
        evento = EventoWebhookFirmaEducativa.objects.select_for_update().get(
            pk=evento.pk
        )
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'artefacto',
            'solicitud',
        ).get(pk=proceso.pk)
        if proceso.estado == EstadoProcesoFirmaEducativa.SIGNED:
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
                codigo='ALREADY_SIGNED',
                proceso=proceso,
            )
            return
        if (
            proceso.estado != EstadoProcesoFirmaEducativa.SENT
            or proceso.solicitud.estado
            != EstadoSolicitudFinanciacion.PENDING_SIGNATURE
        ):
            raise ValidationError('El proceso no admite una confirmacion de firma.')
        artefacto = proceso.artefacto
        if (
            artefacto.tipo
            != TipoArtefactoContractualEducativo.PROMISSORY_NOTE
            or not artefacto.vigente
            or artefacto.estado
            != EstadoArtefactoContractualEducativo.SENT_FOR_SIGNATURE
        ):
            raise ValidationError(
                'La firma no corresponde al pagare vigente de la solicitud.'
            )
        pagare_vigente_id = ArtefactoContractualEducativo.objects.filter(
            solicitud=proceso.solicitud,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            vigente=True,
        ).values_list('pk', flat=True).first()
        if pagare_vigente_id != artefacto.pk:
            raise ValidationError(
                'La firma no corresponde al pagare vigente de la solicitud.'
            )
        ahora = timezone.now()
        artefacto.estado = EstadoArtefactoContractualEducativo.SIGNED
        artefacto.hash_firmado_sha256 = hashlib.sha256(pdf_firmado).hexdigest()
        artefacto.tamano_firmado_bytes = len(pdf_firmado)
        artefacto.firmado_en = ahora
        artefacto.archivo_firmado.save(
            f'{artefacto.numero_documento}-firmado.pdf',
            ContentFile(pdf_firmado),
            save=False,
        )
        nombre_guardado = artefacto.archivo_firmado.name
        try:
            artefacto.full_clean()
            artefacto.save(
                update_fields=[
                    'estado',
                    'archivo_firmado',
                    'hash_firmado_sha256',
                    'tamano_firmado_bytes',
                    'firmado_en',
                    'actualizado_en',
                ]
            )
            proceso.estado = EstadoProcesoFirmaEducativa.SIGNED
            proceso.firmado_en = ahora
            proceso.codigo_ultimo_error = ''
            proceso.save(
                update_fields=[
                    'estado',
                    'firmado_en',
                    'codigo_ultimo_error',
                    'actualizado_en',
                ]
            )
            solicitud = proceso.solicitud
            solicitud.fecha_matricula = timezone.localdate()
            solicitud.save(update_fields=['fecha_matricula', 'actualizada_en'])
            transicionar_solicitud(
                solicitud=solicitud,
                nuevo_estado=EstadoSolicitudFinanciacion.APPROVED,
                motivo='Firma valida del pagare educativo confirmada.',
                metadata={
                    'signature_process_id': str(proceso.pk),
                    'webhook_event_id': str(evento.pk),
                    'course_authorized': True,
                },
            )
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
                codigo='SIGNED_CONFIRMED',
                proceso=proceso,
            )
        except Exception:
            if nombre_guardado and artefacto.archivo_firmado.storage.exists(
                nombre_guardado
            ):
                artefacto.archivo_firmado.storage.delete(nombre_guardado)
            raise


def _registrar_rechazo(*, evento, proceso):
    with transaction.atomic():
        evento = EventoWebhookFirmaEducativa.objects.select_for_update().get(
            pk=evento.pk
        )
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'artefacto',
            'solicitud',
        ).get(pk=proceso.pk)
        if proceso.estado == EstadoProcesoFirmaEducativa.SIGNED:
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
                codigo='SIGNED_PROCESS_NOT_DOWNGRADED',
                proceso=proceso,
            )
            return
        if proceso.estado == EstadoProcesoFirmaEducativa.REFUSED:
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
                codigo='ALREADY_REFUSED',
                proceso=proceso,
            )
            return
        if (
            proceso.estado != EstadoProcesoFirmaEducativa.SENT
            or proceso.solicitud.estado
            != EstadoSolicitudFinanciacion.PENDING_SIGNATURE
        ):
            raise ValidationError('El proceso no admite un rechazo de firma.')
        proceso.estado = EstadoProcesoFirmaEducativa.REFUSED
        proceso.rechazado_en = timezone.now()
        proceso.save(
            update_fields=['estado', 'rechazado_en', 'actualizado_en']
        )
        artefacto = proceso.artefacto
        artefacto.estado = EstadoArtefactoContractualEducativo.CANCELLED
        artefacto.vigente = False
        artefacto.full_clean()
        artefacto.save(update_fields=['estado', 'vigente', 'actualizado_en'])
        transicionar_solicitud(
            solicitud=proceso.solicitud,
            nuevo_estado=EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
            motivo='El responsable contractual rechazo la firma del pagare.',
            metadata={
                'signature_process_id': str(proceso.pk),
                'webhook_event_id': str(evento.pk),
            },
        )
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
            codigo='REFUSAL_RECORDED',
            proceso=proceso,
        )


def _registrar_interrupcion(*, evento, proceso, estado_proceso, codigo):
    with transaction.atomic():
        evento = EventoWebhookFirmaEducativa.objects.select_for_update().get(
            pk=evento.pk
        )
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'artefacto',
            'solicitud',
        ).get(pk=proceso.pk)
        if proceso.estado in {
            EstadoProcesoFirmaEducativa.SIGNED,
            EstadoProcesoFirmaEducativa.REFUSED,
            EstadoProcesoFirmaEducativa.CANCELLED,
            EstadoProcesoFirmaEducativa.EXPIRED,
        }:
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
                codigo='PROCESS_ALREADY_FINAL',
                proceso=proceso,
            )
            return
        if (
            proceso.estado != EstadoProcesoFirmaEducativa.SENT
            or proceso.solicitud.estado
            != EstadoSolicitudFinanciacion.PENDING_SIGNATURE
        ):
            raise ValidationError('El proceso no admite esta interrupcion.')
        proceso.estado = estado_proceso
        proceso.codigo_ultimo_error = codigo
        proceso.save(
            update_fields=['estado', 'codigo_ultimo_error', 'actualizado_en']
        )
        artefacto = proceso.artefacto
        artefacto.estado = EstadoArtefactoContractualEducativo.CANCELLED
        artefacto.vigente = False
        artefacto.full_clean()
        artefacto.save(update_fields=['estado', 'vigente', 'actualizado_en'])
        transicionar_solicitud(
            solicitud=proceso.solicitud,
            nuevo_estado=EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
            motivo='El proceso externo de firma dejo de estar vigente.',
            metadata={
                'signature_process_id': str(proceso.pk),
                'webhook_event_id': str(evento.pk),
                'reason_code': codigo,
            },
        )
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
            codigo=codigo,
            proceso=proceso,
        )


def _registrar_fallo_autenticacion(*, evento, proceso):
    with transaction.atomic():
        evento = EventoWebhookFirmaEducativa.objects.select_for_update().get(
            pk=evento.pk
        )
        proceso = ProcesoFirmaEducativa.objects.select_for_update().select_related(
            'solicitud'
        ).get(pk=proceso.pk)
        if proceso.estado != EstadoProcesoFirmaEducativa.SENT:
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
                codigo='PROCESS_NOT_PENDING_SIGNATURE',
                proceso=proceso,
            )
            return
        proceso.estado = EstadoProcesoFirmaEducativa.FAILED
        proceso.codigo_ultimo_error = 'SIGNER_AUTHENTICATION_FAILED'
        proceso.save(
            update_fields=['estado', 'codigo_ultimo_error', 'actualizado_en']
        )
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
            codigo='SIGNER_AUTHENTICATION_FAILED',
            proceso=proceso,
        )


def _huella_evento_webhook(payload):
    identificador_proveedor = str(payload.get('event_id') or '').strip()
    token = str(payload.get('token') or '').strip()
    if identificador_proveedor:
        identidad = {
            'provider_event_id': identificador_proveedor,
        }
    elif token:
        identidad = {
            'event_type': str(payload.get('event_type') or '').strip().lower(),
            'token': token,
            'external_id': str(payload.get('external_id') or '').strip(),
            'status': str(payload.get('status') or '').strip().lower(),
        }
    else:
        identidad = payload
    canonico = json.dumps(
        identidad,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(canonico).hexdigest()


def procesar_webhook_firma(*, payload, raw_body):
    tipo_evento = str(payload.get('event_type') or '').strip().lower()
    token = str(payload.get('token') or '').strip()
    payload_hash = _huella_evento_webhook(payload)
    evento, creado = _obtener_o_crear_evento(
        payload_hash=payload_hash,
        tipo_evento=tipo_evento or 'unknown',
    )
    if not creado and evento.estado in {
        EstadoEventoWebhookFirmaEducativa.PROCESSED,
        EstadoEventoWebhookFirmaEducativa.IGNORED,
    }:
        return ResultadoWebhookFirma(estado='replayed', codigo=evento.codigo_resultado)
    if not token:
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
            codigo='MISSING_DOCUMENT_TOKEN',
        )
        return ResultadoWebhookFirma(estado='ignored', codigo='MISSING_DOCUMENT_TOKEN')
    if len(tipo_evento) > 50 or len(token) > 160:
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
            codigo='INVALID_EVENT_IDENTIFIERS',
        )
        return ResultadoWebhookFirma(
            estado='ignored',
            codigo='INVALID_EVENT_IDENTIFIERS',
        )
    proceso = ProcesoFirmaEducativa.objects.filter(
        token_documento_externo=token
    ).first()
    if not proceso:
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
            codigo='UNKNOWN_DOCUMENT',
        )
        return ResultadoWebhookFirma(estado='ignored', codigo='UNKNOWN_DOCUMENT')
    if str(payload.get('external_id') or '').strip() != proceso.external_id:
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
            codigo='EXTERNAL_ID_MISMATCH',
            proceso=proceso,
        )
        return ResultadoWebhookFirma(estado='ignored', codigo='EXTERNAL_ID_MISMATCH')

    if tipo_evento == 'doc_signed':
        if str(payload.get('status') or '').strip().lower() != 'signed':
            _marcar_evento(
                evento,
                estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
                codigo='SIGNATURE_NOT_COMPLETE',
                proceso=proceso,
            )
            return ResultadoWebhookFirma(
                estado='ignored',
                codigo='SIGNATURE_NOT_COMPLETE',
            )
        try:
            pdf = _backend().descargar_firmado(
                token_documento=proceso.token_documento_externo
            )
            _finalizar_firma(evento=evento, proceso=proceso, pdf_firmado=pdf)
        except Exception:
            evento.refresh_from_db()
            if evento.estado == EstadoEventoWebhookFirmaEducativa.RECEIVED:
                _marcar_evento(
                    evento,
                    estado=(
                        EstadoEventoWebhookFirmaEducativa.RETRYABLE_ERROR
                    ),
                    codigo='SIGNED_FILE_RECOVERY_FAILED',
                    proceso=proceso,
                )
            raise
        return ResultadoWebhookFirma(estado='processed', codigo='SIGNED_CONFIRMED')

    if tipo_evento == 'doc_refused':
        _registrar_rechazo(evento=evento, proceso=proceso)
        if settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
            from financiacion_educativa.services.orquestacion_automatica import (
                ejecutar_orquestacion_automatica_segura,
            )

            ejecutar_orquestacion_automatica_segura(
                solicitud_id=proceso.solicitud_id
            )
        return ResultadoWebhookFirma(estado='processed', codigo='REFUSAL_RECORDED')

    if tipo_evento in {'doc_deleted', 'doc_cancelled'}:
        _registrar_interrupcion(
            evento=evento,
            proceso=proceso,
            estado_proceso=EstadoProcesoFirmaEducativa.CANCELLED,
            codigo='SIGNATURE_CANCELLED',
        )
        return ResultadoWebhookFirma(estado='processed', codigo='SIGNATURE_CANCELLED')

    if tipo_evento == 'doc_expired':
        _registrar_interrupcion(
            evento=evento,
            proceso=proceso,
            estado_proceso=EstadoProcesoFirmaEducativa.EXPIRED,
            codigo='SIGNATURE_EXPIRED',
        )
        return ResultadoWebhookFirma(estado='processed', codigo='SIGNATURE_EXPIRED')

    if tipo_evento == 'signer_authentication_failed':
        _registrar_fallo_autenticacion(evento=evento, proceso=proceso)
        return ResultadoWebhookFirma(
            estado='processed',
            codigo='SIGNER_AUTHENTICATION_FAILED',
        )

    if tipo_evento == 'doc_created':
        _marcar_evento(
            evento,
            estado=EstadoEventoWebhookFirmaEducativa.PROCESSED,
            codigo='DOCUMENT_CREATED_CONFIRMED',
            proceso=proceso,
        )
        return ResultadoWebhookFirma(
            estado='processed',
            codigo='DOCUMENT_CREATED_CONFIRMED',
        )

    _marcar_evento(
        evento,
        estado=EstadoEventoWebhookFirmaEducativa.IGNORED,
        codigo='EVENT_NOT_ACTIONABLE',
        proceso=proceso,
    )
    return ResultadoWebhookFirma(estado='ignored', codigo='EVENT_NOT_ACTIONABLE')
