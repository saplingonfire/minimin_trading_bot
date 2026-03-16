"""Exceptions for the Roostoo Public API SDK."""


class RoostooAPIError(Exception):
    """Raised on HTTP errors or when the API returns Success: false."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
        raw: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        self.raw = raw
