import hashlib
import hmac
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from payment_gateway_api.api.schemas.payment_schema import ProcessPaymentCommand
from payment_gateway_api.config import get_settings
from payment_gateway_api.domain.errors import (
    IdempotencyConflictError,
    PaymentNotFoundError,
)
from payment_gateway_api.domain.models.payment import Payment, PaymentStatus
from payment_gateway_api.domain.protocols.idempotency_store import (
    IdempotencyStore,
)
from payment_gateway_api.domain.protocols.payment_repository import PaymentRepository
from payment_gateway_api.infrastructure.clients.acquiring_bank import (
    AcquiringBankClient,
)
from payment_gateway_api.infrastructure.clients.models import AuthorizationRequest

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(
        self,
        repository: PaymentRepository,
        idempotency_store: IdempotencyStore,
        bank_client: AcquiringBankClient,
    ) -> None:
        self._repository = repository
        self._idempotency_store = idempotency_store
        self._bank_client = bank_client

    async def process_payment(self, command: ProcessPaymentCommand) -> Payment:
        key = command.idempotency_key
        if key:
            replayedPayment = await self._claim_idempotency_key(command)
            if replayedPayment:
                logger.info(
                    "payment.idempotent_replay",
                    extra={
                        "payment_id": str(replayedPayment.id),
                        "merchant_id": command.merchant_id,
                    },
                )
                return replayedPayment

        authorization_request = AuthorizationRequest(
            card_number=command.card_number,
            expiry_month=command.expiry_month,
            expiry_year=command.expiry_year,
            currency=command.currency,
            amount=command.amount,
            cvv=command.cvv,
        )

        try:
            res = await self._bank_client.authorize(authorization_request)
        except Exception:
            if key:
                await self._idempotency_store.release(command.merchant_id, key)

            raise

        payment = Payment(
            id=uuid4(),
            merchant_id=command.merchant_id,
            status=PaymentStatus.AUTHORIZED
            if res.authorized
            else PaymentStatus.DECLINED,
            last_four_card_digits=authorization_request.last_four_digits,
            expiry_month=command.expiry_month,
            expiry_year=command.expiry_year,
            currency=command.currency,
            amount=command.amount,
            created_at=datetime.now(UTC),
        )

        await self._repository.add(payment)
        if key:
            await self._idempotency_store.complete(command.merchant_id, key, payment.id)

        logger.info(
            "payment.processed",
            extra={
                "payment_id": str(payment.id),
                "merchant_id": payment.merchant_id,
                "status": payment.status.value,
                "amount": payment.amount,
                "currency": payment.currency,
                "last_four_card_digits": payment.last_four_card_digits,
            },
        )
        return payment

    async def _claim_idempotency_key(
        self, command: ProcessPaymentCommand
    ) -> Payment | None:
        fingerprint = self._generate_fingerprint(command)
        assert command.idempotency_key is not None
        record = await self._idempotency_store.reserve(
            command.merchant_id, command.idempotency_key, fingerprint
        )
        if not record:
            return record

        if record.request_fingerprint != fingerprint:
            raise IdempotencyConflictError(
                "This Idempotency key has already been used with a different request"
            )

        if not record.payment_id:
            raise IdempotencyConflictError(
                "A request with this idempotency key is already in progress"
            )

        # we get the payment from the db
        payment = await self._repository.get(record.payment_id, command.merchant_id)
        if not payment:
            raise IdempotencyConflictError(
                "Could not retrieve this idempotency key -normally not reachebale-"
            )

        return payment

    def _generate_fingerprint(self, command: ProcessPaymentCommand) -> str:
        canonical_payload = "|".join(
            (
                command.merchant_id,
                command.card_number,
                str(command.expiry_month),
                str(command.expiry_year),
                command.currency,
                str(command.amount),
            )
        ).encode("utf-8")

        return hmac.new(
            get_settings().idempotency_secret_key.encode("utf-8"),
            canonical_payload,
            hashlib.sha256,
        ).hexdigest()

    async def get_payment(self, payment_id: UUID, merchant_id: str) -> Payment:
        payment = await self._repository.get(payment_id, merchant_id)
        if not payment:
            raise PaymentNotFoundError(f"No payment with id {payment_id}")
        return payment
