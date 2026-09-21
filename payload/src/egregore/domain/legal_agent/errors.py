# epistemic marker: provenance / auditability
class RegistryValidationError(ValueError):
    """Raised when projection registry validation fails."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail or message or message
        self.message = message
