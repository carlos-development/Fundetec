class RecordingInvitationDeliveryBackend:
    deliveries = []

    @classmethod
    def reset(cls):
        cls.deliveries = []

    def deliver(self, *, recipient, continuation_url, expires_at):
        self.__class__.deliveries.append({
            'recipient': recipient,
            'continuation_url': continuation_url,
            'expires_at': expires_at,
        })


class FailingInvitationDeliveryBackend(RecordingInvitationDeliveryBackend):
    def deliver(self, *, recipient, continuation_url, expires_at):
        super().deliver(
            recipient=recipient,
            continuation_url=continuation_url,
            expires_at=expires_at,
        )
        raise RuntimeError('Fallo de entrega simulado.')
