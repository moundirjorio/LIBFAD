"""Exports Excel (et CSV) via openpyxl."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Sequence

from openpyxl import Workbook


def to_xlsx(rows: Iterable[Sequence], headers: Sequence[str], sheet_name: str = "Export") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    # auto-width approximatif
    for col_idx, header in enumerate(headers, 1):
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[col_letter].width = max(12, min(40, len(header) + 2))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
