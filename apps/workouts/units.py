"""Weight unit helpers.

Weights are stored exactly as the athlete entered them, alongside the unit they
entered them in. That means switching a profile from kg to lb never silently
rewrites history, and a set logged in lb stays readable in lb forever.

Comparisons (PR detection, volume roll-ups) normalise through :func:`to_kg`.
"""

from __future__ import annotations

from decimal import Decimal

KG = "kg"
LB = "lb"

LB_TO_KG = Decimal("0.45359237")

VALID_UNITS = {KG, LB}


def to_kg(value, unit: str) -> Decimal:
    """Normalise ``value`` to kilograms."""
    if value is None:
        return Decimal("0")
    amount = Decimal(str(value))
    if unit == LB:
        return (amount * LB_TO_KG).quantize(Decimal("0.001"))
    return amount


def from_kg(value, unit: str) -> Decimal:
    """Render a kilogram quantity in ``unit``."""
    if value is None:
        return Decimal("0")
    amount = Decimal(str(value))
    if unit == LB:
        return (amount / LB_TO_KG).quantize(Decimal("0.01"))
    return amount.quantize(Decimal("0.01"))


def convert(value, from_unit: str, to_unit: str) -> Decimal:
    """Convert between units without double-quantising the intermediate value."""
    if from_unit == to_unit:
        return Decimal(str(value or 0))
    if to_unit == KG:
        return to_kg(value, from_unit)
    return from_kg(to_kg(value, from_unit), to_unit)


def normalise_unit(unit: str | None) -> str:
    """Coerce anything unexpected to kg, which is the safer default."""
    candidate = (unit or KG).strip().lower()
    return candidate if candidate in VALID_UNITS else KG
