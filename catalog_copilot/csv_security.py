"""Spreadsheet formula injection defense and CSV sanitization for CatalogCopilot.

Protects against CSV / Excel Formula Injection (DDE attacks) by neutralizing
cells that begin with '=', '+', '-', '@', tab, or carriage return characters,
ensuring safe export into spreadsheet software like Microsoft Excel and LibreOffice Calc.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC_PATTERN = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def is_potentially_dangerous_formula(val: Any) -> bool:
    """Checks whether a cell value begins with a formula trigger character without being a simple number."""
    if val is None or not isinstance(val, str):
        return False
    if not val:
        return False
    # Directly detect leading control / tab characters without stripping
    if val.startswith(("\t", "\r")):
        return True
    s = val.strip()
    if not s:
        return False
    if s.startswith(_DANGEROUS_PREFIXES):
        # Exclude benign strictly numeric literals (e.g. '-5', '+10', '-120.50')
        if _NUMERIC_PATTERN.match(s):
            return False
        return True
    return False


def sanitize_csv_cell(val: Any) -> str:
    """Sanitizes a single cell value for CSV export, prepending a single quote if dangerous."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)

    s = str(val)
    if is_potentially_dangerous_formula(s):
        return f"'{s}"
    return s


def desanitize_csv_cell(val: str) -> str:
    """Reverses CSV formula sanitization if a leading quote was prepended for escaping."""
    if not val:
        return ""
    if val.startswith("'") and len(val) > 1:
        rest = val[1:]
        if is_potentially_dangerous_formula(rest):
            return rest
    return val


def sanitize_csv_row(row: Dict[str, Any]) -> Dict[str, str]:
    """Sanitizes all cell values in a dictionary row for safe CSV writing."""
    return {k: sanitize_csv_cell(v) for k, v in row.items()}


def desanitize_csv_row(row: Dict[str, str]) -> Dict[str, str]:
    """Desanitizes all cell values in a dictionary row read from a sanitized CSV."""
    return {k: desanitize_csv_cell(v) for k, v in row.items()}
