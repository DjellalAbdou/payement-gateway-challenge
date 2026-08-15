class PaymentGatewayError(Exception):
    """Base class for all payment gateway errors."""


class AcquiringBankError(PaymentGatewayError):
    """the answer from the acquiring bank is not what we expected, or we could not reach it."""
