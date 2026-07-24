from django.db import models


class EstadoSolicitudFinanciacion(models.TextChoices):
    PENDING_USER_REGISTRATION = 'PENDING_USER_REGISTRATION', 'Pendiente de registro del usuario'
    PENDING_TERMS = 'PENDING_TERMS', 'Pendiente de terminos'
    PENDING_DOCUMENT = 'PENDING_DOCUMENT', 'Pendiente de documento'
    PENDING_GUARDIAN = 'PENDING_GUARDIAN', 'Pendiente de tutor'
    PENDING_MANUAL_REVIEW = 'PENDING_MANUAL_REVIEW', 'Pendiente de revision manual'
    PENDING_PROMISSORY_NOTE = 'PENDING_PROMISSORY_NOTE', 'Pendiente de pagare'
    PENDING_SIGNATURE = 'PENDING_SIGNATURE', 'Pendiente de firma'
    ACTIVE = 'ACTIVE', 'Activa'
    PAYMENT_REPORTED = 'PAYMENT_REPORTED', 'Pago reportado'
    PAYMENT_UNDER_REVIEW = 'PAYMENT_UNDER_REVIEW', 'Pago en revision'
    PAID = 'PAID', 'Pagada'
    CANCELLED = 'CANCELLED', 'Cancelada'


class RolParticipante(models.TextChoices):
    STUDENT = 'STUDENT', 'Estudiante'
    GUARDIAN = 'GUARDIAN', 'Tutor'
    PRINCIPAL_DEBTOR = 'PRINCIPAL_DEBTOR', 'Deudor principal'


class TipoDocumentoIdentidad(models.TextChoices):
    CC = 'CC', 'Cedula de ciudadania'
    TI = 'TI', 'Tarjeta de identidad'
    CE = 'CE', 'Cedula de extranjeria'
    RC = 'RC', 'Registro civil'
    PASSPORT = 'PASSPORT', 'Pasaporte'
    OTHER = 'OTHER', 'Otro'


class TipoConsentimiento(models.TextChoices):
    TERMS = 'TERMS', 'Terminos y condiciones'
    DATA_PROCESSING = 'DATA_PROCESSING', 'Tratamiento de datos'
    CREDIT_AUTHORIZATION = 'CREDIT_AUTHORIZATION', 'Autorizacion de financiacion'


class TipoDocumentoFinanciacion(models.TextChoices):
    STUDENT_ID_FRONT = 'STUDENT_ID_FRONT', 'Documento frontal del estudiante'
    GUARDIAN_ID_FRONT = 'GUARDIAN_ID_FRONT', 'Documento frontal del tutor'
    OTHER = 'OTHER', 'Otro'


class EstadoValidacionDocumento(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente'
    APPROVED = 'APPROVED', 'Aprobado'
    REJECTED = 'REJECTED', 'Rechazado'


class OrigenCapturaDocumento(models.TextChoices):
    INSTITUTION_API = 'INSTITUTION_API', 'API institucional'
    USER_UPLOAD = 'USER_UPLOAD', 'Carga del usuario'
    CAMERA = 'CAMERA', 'Camara'
    INTERNAL = 'INTERNAL', 'Generado internamente'


class MetodoCalculoFinanciero(models.TextChoices):
    FRENCH_AMORTIZATION = 'FRENCH_AMORTIZATION', 'Anualidad francesa'
