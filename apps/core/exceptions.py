"""Uniform API error envelope.

Every non-2xx response the Flutter client can receive looks like::

    {"error": {"code": "validation_error", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class ConflictError(APIException):
    """Raised when a push targets a row that changed on another device."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This record was modified on another device."
    default_code = "conflict"


class SyncError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Malformed sync payload."
    default_code = "sync_error"


def _flatten(detail):
    """Turn DRF's nested detail structures into a flat ``{field: [msg, ...]}``."""
    if isinstance(detail, dict):
        flat = {}
        for key, value in detail.items():
            if isinstance(value, (list, tuple)):
                flat[key] = [str(item) for item in value]
            elif isinstance(value, dict):
                flat[key] = _flatten(value)
            else:
                flat[key] = [str(value)]
        return flat
    if isinstance(detail, (list, tuple)):
        return {"non_field_errors": [str(item) for item in detail]}
    return {"non_field_errors": [str(detail)]}


def api_exception_handler(exc, context):
    if isinstance(exc, ValidationError):
        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": "The submitted data was invalid.",
                    "details": _flatten(exc.detail),
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    code = getattr(exc, "default_code", "error")
    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
    else:
        message = "The request could not be completed."
        code = "validation_error" if response.status_code == 400 else str(code)

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": (
                _flatten(detail) if not isinstance(detail, dict) or "detail" not in detail else {}
            ),
        }
    }
    return response
