class AdapterError(Exception):
    """Only the safe code, never a provider payload, may enter operational logs."""
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)
