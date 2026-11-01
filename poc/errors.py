class ConversionError(Exception):
    """Raised for files we cannot or will not convert. `code` is stable, `retryable` says if a retry could help."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
