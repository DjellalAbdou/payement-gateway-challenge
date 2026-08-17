class PaymentGatewayError(Exception):
    """Base class for all payment gateway errors."""


class AcquiringBankError(PaymentGatewayError):
    """The bank did not give us a usable answer.

    In every case the outcome is not a decline, so nothing is persisted. What
    differs between the subclasses is whether the payment might nonetheless have
    been taken, which decides both the status code the merchant sees and whether
    their idempotency key can be freed for a retry.

    Never raised directly; one of the subclasses below always applies.
    """


class AcquiringBankTimeoutError(AcquiringBankError):
    """The request was delivered but no answer arrived in time.

    The most dangerous case: the bank may have authorized the payment and we simply
    never saw the response. Retrying blindly risks charging the shopper twice, so
    the idempotency key stays claimed and the merchant is told the outcome is
    unknown rather than being invited to retry.
    """

    definitely_not_processed = False


class AcquiringBankUnavailableError(AcquiringBankError):
    """The bank refused the request before processing it, or was never reached.

    Connection failures and a 503 both prove no payment was taken, so the merchant
    can safely retry with the same idempotency key.
    """

    definitely_not_processed = True


class AcquiringBankProtocolError(AcquiringBankError):
    """The bank answered, but not in a way we can use.

    An unexpected status code, or a body we cannot parse. The bank rejecting our
    request as malformed lands here too: since our own validation makes every field
    required, the bank can only complain about a missing field if we built the
    request wrongly.

    That makes this a bug in our integration rather than an outage, so it is logged
    loudly for us and the merchant is deliberately not invited to retry -- a retry
    would fail in exactly the same way until we ship a fix.
    """

    definitely_not_processed = False


class IdempotencyConflictError(PaymentGatewayError):
    """merchant called the gateway with the same idempotency key but the content is different"""


class PaymentNotFoundError(PaymentGatewayError):
    """No payment with that id exists for the requesting merchant."""
