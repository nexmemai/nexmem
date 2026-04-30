"""SDK exceptions."""


class NexMemError(Exception):
    """Base SDK exception."""


class NexMemAPIError(NexMemError):
    """Raised when the NexMem API returns an error response."""

    def __init__(self, status_code: int, message: str, response: object = None):
        self.status_code = status_code
        self.response = response
        super().__init__(f"NexMem API error {status_code}: {message}")


class NexMemAuthError(NexMemAPIError):
    """Raised for authentication or authorization failures."""
