"""Backend Google Sheets (ao vivo) via Service Account.

Lê os veículos, os AIs já registrados e a tabela de órgãos direto da planilha online,
e grava as multas novas na aba CONTROLE DE MULTAS. Usa a mesma lógica de parsing dos
outros backends (`vehicles_source` e `controle_multas`), mudando só a origem/destino.

Setup (uma vez):
  1. Google Cloud → criar projeto → habilitar "Google Sheets API".
  2. Criar uma Service Account → gerar chave JSON → salvar como `credentials.json`.
  3. Compartilhar a planilha como **Editor** com o e-mail da Service Account
     (algo como ...@...iam.gserviceaccount.com).
"""
from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from controle_multas import (
    SHEET_CONTROLE,
    SHEET_ORGAO,
    MultaRow,
    existing_ait_from_rows,
    orgao_lookup_from_rows,
)
from vehicles_source import SHEET_NAME as SHEET_LISTA
from vehicles_source import vehicles_from_rows

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# ID da planilha "CONTROLE DE MULTAS VICTOR".
DEFAULT_SPREADSHEET_ID = "1OHV-PBgaO01URo0ZmnvlvHk-UQVZuvV0fOxmc8O2ozI"


def open_spreadsheet(credentials_path: str, spreadsheet_id: str = DEFAULT_SPREADSHEET_ID):
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(spreadsheet_id)


def _values(spreadsheet, sheet_name):
    """Lê a aba com valores 'crus' (números como números), evitando problemas de
    formatação (ex.: renavam exibido com separador de milhar)."""
    return spreadsheet.worksheet(sheet_name).get_all_values(
        value_render_option="UNFORMATTED_VALUE"
    )


def read_vehicles(spreadsheet, limit=None):
    return vehicles_from_rows(_values(spreadsheet, SHEET_LISTA), limit=limit)


def read_existing_ait(spreadsheet) -> set[str]:
    return existing_ait_from_rows(_values(spreadsheet, SHEET_CONTROLE))


def read_orgao_lookup(spreadsheet) -> dict[str, str]:
    return orgao_lookup_from_rows(_values(spreadsheet, SHEET_ORGAO))


class SheetsSink:
    """Acumula as multas novas e faz um único append na aba CONTROLE DE MULTAS."""

    def __init__(self, spreadsheet):
        self.ws = spreadsheet.worksheet(SHEET_CONTROLE)
        self._rows: list[list] = []
        self.count = 0

    def append(self, row: MultaRow) -> None:
        self._rows.append(row.to_cells())
        self.count += 1

    def save(self) -> None:
        if self._rows:
            # USER_ENTERED: o Sheets interpreta datas/números conforme o locale da planilha.
            self.ws.append_rows(self._rows, value_input_option="USER_ENTERED")
