"""Errors raised by the pipeline. `code` is an i18n key suffix (`error.<code>`), `params` fill its placeholders."""
from __future__ import annotations


class PipelineError(Exception):
    def __init__(self, code: str, message: str = "", **params):
        self.code = code
        self.params = params
        super().__init__(message or code)

    def to_dict(self) -> dict:
        return dict(code=self.code, params=self.params, message=str(self))


class Cancelled(BaseException):
    """Raised at checkpoints after the user cancels a run. BaseException so generic `except Exception`
    handlers inside the pipeline steps do not swallow it."""
