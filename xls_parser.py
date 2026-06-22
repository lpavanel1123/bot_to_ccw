"""
Lê o XLS do ccwbot e extrai a data mais distante em 'Estimated Delivery Date'.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

import openpyxl


def _find_header_row(ws) -> Optional[int]:
    """Localiza a linha que contém os cabeçalhos das colunas de linha de pedido."""
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == "Estimated Delivery Date":
                return cell.row
    return None


def _find_col(ws, header_row: int, column_name: str) -> Optional[str]:
    """Retorna a letra da coluna onde está o cabeçalho informado."""
    for cell in ws[header_row]:
        if cell.value == column_name:
            return cell.column_letter
    return None


def _parse_date(value) -> Optional[datetime]:
    if not value or str(value).strip() == "":
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def latest_estimated_delivery(file_path: str) -> Optional[datetime]:
    """
    Abre o XLS/XLSX do ccwbot e retorna a data mais distante em
    'Estimated Delivery Date' entre todas as linhas de pedido.
    """
    path = Path(file_path)

    # Arquivo vem com extensão .xls mas é OOXML (ZIP) — passa como BytesIO
    # para openpyxl não rejeitar pela extensão
    wb = openpyxl.load_workbook(BytesIO(path.read_bytes()), data_only=True)
    ws = wb.active

    header_row = _find_header_row(ws)
    if header_row is None:
        raise ValueError("Coluna 'Estimated Delivery Date' não encontrada no arquivo.")

    col_letter = _find_col(ws, header_row, "Estimated Delivery Date")
    if col_letter is None:
        raise ValueError("Coluna 'Estimated Delivery Date' não encontrada no cabeçalho.")

    dates = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=False):
        cell = ws[f"{col_letter}{row[0].row}"]
        parsed = _parse_date(cell.value)
        if parsed:
            dates.append(parsed)

    return max(dates) if dates else None


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "order_119659099.xls"
    result = latest_estimated_delivery(path)
    print(f"Estimated Delivery Date mais distante: {result.strftime('%d-%b-%Y') if result else 'N/A'}")
