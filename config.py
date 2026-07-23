"""Configuração persistida do aplicativo.

Guarda tudo num diretório do usuário (não no diretório de trabalho), porque uma execução
agendada roda com o cwd imprevisível:

    Windows  %APPDATA%\\DetranExtractor
    macOS    ~/Library/Application Support/DetranExtractor
    Linux    ~/.config/DetranExtractor

Dentro dele: `config.json`, o `credentials.json` importado pela tela, o profile do Chrome
(`chrome-profile/`), os logs das execuções agendadas e o `history.jsonl`.

Os nomes dos campos de `Config` são os mesmos que a CLI usava no `argparse`, de propósito:
`main.load_data()` continua funcionando recebendo um `Config` no lugar do `args`.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

APP_NAME = "DetranExtractor"

DEFAULT_XLSX = "CONTROLE DE MULTAS VICTOR.xlsx"
DEFAULT_OUT_XLSX = "CONTROLE DE MULTAS VICTOR - TESTE.xlsx"
DEFAULT_SPREADSHEET_ID = "1OHV-PBgaO01URo0ZmnvlvHk-UQVZuvV0fOxmc8O2ozI"
DEFAULT_CDP_PORT = 9222


def app_dir() -> Path:
    """Diretório de dados do app, criado sob demanda."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_dir() / "config.json"


def credentials_path() -> Path:
    """Destino do `credentials.json` importado pela tela."""
    return app_dir() / "credentials.json"


def profile_path() -> Path:
    """Profile do Chrome que mantém a sessão do gov.br viva entre execuções."""
    return app_dir() / "chrome-profile"


def logs_dir() -> Path:
    path = app_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Config:
    # --- origem/destino
    source: str = "sheets"                      # "xlsx" | "sheets"
    credentials: str = ""                       # caminho do JSON da Service Account

    # Campos legados de planilha única (mantidos por compatibilidade e como default);
    # a fila real vive em `sheet_targets` / `xlsx_targets`, semeada a partir deles no load().
    spreadsheet_id: str = DEFAULT_SPREADSHEET_ID
    xlsx: str = DEFAULT_XLSX
    out_xlsx: str = DEFAULT_OUT_XLSX

    # --- fila de planilhas (uma por fonte). Cada item é um dict:
    #   sheet_targets: {"nome": str, "spreadsheet_id": str}
    #   xlsx_targets:  {"nome": str, "xlsx": str, "out_xlsx": str}
    sheet_targets: list = field(default_factory=list)
    xlsx_targets: list = field(default_factory=list)

    # --- execução
    limit: int = 5
    dry_run: bool = False
    min_delay: float = 6.0
    max_delay: float = 15.0
    cdp_port: int = DEFAULT_CDP_PORT
    profile_dir: str = field(default_factory=lambda: str(profile_path()))

    # --- agendamento (só informativo; quem manda é o agendador do SO)
    schedule_time: str = "08:00"

    # ------------------------------------------------------------------ persistência

    @classmethod
    def load(cls) -> "Config":
        """Lê o config.json; campos desconhecidos ou ausentes caem no default."""
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg._migrate_targets()
        return cfg

    def _migrate_targets(self) -> None:
        """Semeia a fila a partir dos campos legados quando ela vier vazia.

        Garante que configs antigos (só `spreadsheet_id` / `xlsx`) continuem funcionando:
        a primeira planilha da fila é derivada do campo único que já existia.
        """
        if not self.sheet_targets and self.spreadsheet_id.strip():
            self.sheet_targets = [
                {"nome": "Planilha principal", "spreadsheet_id": self.spreadsheet_id.strip()}
            ]
        if not self.xlsx_targets and self.xlsx.strip():
            self.xlsx_targets = [
                {"nome": Path(self.xlsx).stem or "Planilha",
                 "xlsx": self.xlsx, "out_xlsx": self.out_xlsx}
            ]

    def save(self) -> Path:
        path = config_path()
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------ fila de planilhas

    def active_targets(self) -> list[dict]:
        """A fila da fonte selecionada (`sheet_targets` ou `xlsx_targets`)."""
        return self.sheet_targets if self.source == "sheets" else self.xlsx_targets

    def iter_target_configs(self) -> list[tuple[str, "Config"]]:
        """Um `(nome, Config)` por planilha da fila ativa.

        Reaproveita este `Config` como perfil de execução: cada alvo é uma cópia com só a
        localização trocada, de forma que `runner.load_data` continua lendo os mesmos campos.
        """
        out: list[tuple[str, "Config"]] = []
        for i, t in enumerate(self.active_targets(), 1):
            nome = (t.get("nome") or f"Planilha {i}").strip()
            if self.source == "sheets":
                cfg = replace(self, spreadsheet_id=str(t.get("spreadsheet_id", "")).strip())
            else:
                cfg = replace(self, xlsx=t.get("xlsx", ""), out_xlsx=t.get("out_xlsx", ""))
            out.append((nome, cfg))
        return out

    # ------------------------------------------------------------------ validação

    def _validate_common(self) -> list[str]:
        """Valida o que independe da planilha específica (opções globais e credenciais)."""
        problemas: list[str] = []

        if self.source not in ("xlsx", "sheets"):
            problemas.append(f"Origem inválida: {self.source!r}.")

        if self.source == "sheets":
            if not self.credentials:
                problemas.append("Escolha o arquivo credentials.json da Service Account.")
            elif not Path(self.credentials).exists():
                problemas.append(f"credentials.json não encontrado em: {self.credentials}")

        if self.limit <= 0:
            problemas.append("O limite de veículos precisa ser maior que zero.")
        if self.min_delay < 0 or self.max_delay < 0:
            problemas.append("Os tempos de espera não podem ser negativos.")
        if self.min_delay > self.max_delay:
            problemas.append("A espera mínima não pode ser maior que a máxima.")
        if not (1 <= self.cdp_port <= 65535):
            problemas.append("Porta do Chrome inválida (use 1–65535).")

        return problemas

    def _validate_sheet(self, spreadsheet_id: str, prefixo: str = "") -> list[str]:
        if not str(spreadsheet_id).strip():
            return [f"{prefixo}Informe o ID da planilha do Google Sheets."]
        return []

    def _validate_xlsx(self, xlsx: str, out_xlsx: str, prefixo: str = "") -> list[str]:
        problemas: list[str] = []
        if not xlsx:
            problemas.append(f"{prefixo}Informe o arquivo .xlsx de entrada.")
        elif not Path(xlsx).exists():
            problemas.append(f"{prefixo}Arquivo .xlsx não encontrado em: {xlsx}")
        if not self.dry_run and not out_xlsx:
            problemas.append(f"{prefixo}Informe o arquivo .xlsx de saída.")
        return problemas

    def validate(self) -> list[str]:
        """Problemas de uma execução de planilha única; vazia = pronto para rodar."""
        problemas = self._validate_common()
        if self.source == "sheets":
            problemas += self._validate_sheet(self.spreadsheet_id)
        else:
            problemas += self._validate_xlsx(self.xlsx, self.out_xlsx)
        return problemas

    def validate_targets(self) -> list[str]:
        """Problemas da fila inteira; vazia = pronto para rodar todas as planilhas."""
        problemas = self._validate_common()
        alvos = self.active_targets()
        if not alvos:
            problemas.append("Adicione ao menos uma planilha à fila.")
            return problemas
        for i, t in enumerate(alvos, 1):
            nome = (t.get("nome") or f"#{i}").strip()
            prefixo = f"[{nome}] "
            if self.source == "sheets":
                problemas += self._validate_sheet(t.get("spreadsheet_id", ""), prefixo)
            else:
                problemas += self._validate_xlsx(t.get("xlsx", ""), t.get("out_xlsx", ""), prefixo)
        return problemas
