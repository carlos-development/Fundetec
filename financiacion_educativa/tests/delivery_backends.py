class RecordingInvitationDeliveryBackend:
    deliveries = []

    @classmethod
    def reset(cls):
        cls.deliveries = []

    def deliver(
        self, *, recipient, continuation_url, expires_at, message_id=None
    ):
        self.__class__.deliveries.append({
            'recipient': recipient,
            'continuation_url': continuation_url,
            'expires_at': expires_at,
            'message_id': message_id,
        })


class FailingInvitationDeliveryBackend(RecordingInvitationDeliveryBackend):
    def deliver(
        self, *, recipient, continuation_url, expires_at, message_id=None
    ):
        super().deliver(
            recipient=recipient,
            continuation_url=continuation_url,
            expires_at=expires_at,
            message_id=message_id,
        )
        raise RuntimeError('Fallo de entrega simulado.')


class RecordingMobileCaptureDeliveryBackend:
    deliveries = []

    @classmethod
    def reset(cls):
        cls.deliveries = []

    def deliver(
        self, *, recipient, continuation_url, expires_at, message_id=None
    ):
        self.__class__.deliveries.append({
            'recipient': recipient,
            'continuation_url': continuation_url,
            'expires_at': expires_at,
            'message_id': message_id,
        })


class FailingMobileCaptureDeliveryBackend:
    def deliver(self, **_kwargs):
        raise RuntimeError('Fallo de entrega movil simulado.')


class FailingDjangoEmailBackend:
    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, _email_messages):
        raise RuntimeError('Fallo de correo simulado.')
