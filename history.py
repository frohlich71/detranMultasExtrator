"""Histórico das execuções, em JSON Lines no diretório do app.

Uma linha por execução. É append-only e tolerante a arquivo corrompido: linha inválida é
simplesmente ignorada na leitura, para o histórico nunca derrubar a tela.
"""
from __future__ import annotations

import json
from datetime import datetime

from config import app_dir

MAX_LINHAS = 500


def history_path():
    return app_dir() / "history.jsonl"


def record(result, cfg, origem: str = "manual", planilha: str = "") -> None:
    """Registra o resultado de uma execução (`runner.RunResult`).

    `planilha` identifica o alvo quando a execução percorre uma fila de planilhas.
    """
    entry = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        "origem": origem,               # "manual" (tela) ou "agendado"
        "planilha": planilha,
        "source": cfg.source,
        "dry_run": cfg.dry_run,
        "veiculos": result.veiculos,
        "novas": result.novas,
        "erros": result.erros,
        "ok": result.ok,
        "cancelado": result.cancelado,
        "mensagem": result.mensagem,
    }
    path = history_path()
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return
    _trim(path)


def _trim(path) -> None:
    """Mantém o arquivo pequeno, guardando só as últimas MAX_LINHAS execuções."""
    try:
        linhas = path.read_text(encoding="utf-8").splitlines()
        if len(linhas) > MAX_LINHAS:
            path.write_text("\n".join(linhas[-MAX_LINHAS:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_recent(n: int = 20) -> list[dict]:
    """Últimas `n` execuções, da mais recente para a mais antiga."""
    path = history_path()
    if not path.exists():
        return []
    try:
        linhas = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for linha in linhas[-n:]:
        try:
            entries.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries
