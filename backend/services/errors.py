"""Domain error raised by services/routers; main.py turns it into the error envelope."""


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str,
                 field: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        self.retryable = retryable
