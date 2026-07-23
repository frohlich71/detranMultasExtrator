#!/usr/bin/env python3
"""Extractor de multas DETRAN-RS — interface de linha de comando.

Lê a lista de veículos (xlsx local ou Google Sheets), consulta as infrações de cada um no
site do DETRAN-RS e grava as pendentes novas no destino escolhido.

O site exige login no gov.br e guarda o token no sessionStorage. Por isso o programa
mantém um **Chrome real vivo** (com porta de debug) e se **anexa** a ele via CDP,
reaproveitando a sessão já autenticada. Faça o login uma vez na janela do Chrome que
abrir; enquanto essa janela ficar aberta, as próximas execuções nem pedem login.

Uso:
    python main.py --login          # abre o Chrome e espera você logar no gov.br
    python main.py --limit 5        # consulta os 5 primeiros veículos

Para a versão com tela (e agendamento), use `python app.py`. A pipeline em si mora em
`runner.py`; aqui só traduzimos os argumentos para um `Config`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import runner
from config import (
    DEFAULT_CDP_PORT,
    DEFAULT_OUT_XLSX,
    DEFAULT_SPREADSHEET_ID,
    DEFAULT_XLSX,
    Config,
    profile_path,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Consulta multas do DETRAN-RS para uma lista de veículos.")
    p.add_argument("--xlsx", action="append", metavar="ARQUIVO",
                   help=f"Arquivo .xlsx de entrada; repita para enfileirar vários (default: {DEFAULT_XLSX})")
    p.add_argument("--limit", type=int, default=5, help="Máximo de veículos a consultar (default: %(default)s)")
    p.add_argument("--min-delay", type=float, default=6.0, help="Espera mínima entre veículos, em segundos")
    p.add_argument("--max-delay", type=float, default=15.0, help="Espera máxima entre veículos, em segundos")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT, help="Porta de debug do Chrome (default: %(default)s)")
    p.add_argument("--source", choices=["xlsx", "sheets"], default="xlsx",
                   help="De onde ler os veículos e onde gravar as multas (default: %(default)s)")
    p.add_argument("--credentials", default="credentials.json",
                   help="JSON da Service Account (apenas para --source sheets)")
    p.add_argument("--spreadsheet-id", action="append", metavar="ID",
                   help="ID da planilha do Google Sheets; repita para enfileirar várias "
                        f"(apenas para --source sheets; default: {DEFAULT_SPREADSHEET_ID})")
    p.add_argument("--out-xlsx", action="append", metavar="ARQUIVO",
                   help="Arquivo .xlsx de saída das multas novas; pareado por ordem com cada "
                        f"--xlsx (--source xlsx; default: {DEFAULT_OUT_XLSX})")
    p.add_argument("--profile-dir", default=None,
                   help="Diretório do profile do Chrome (default: o do app)")
    p.add_argument("--dry-run", action="store_true",
                   help="Só mostra o que seria adicionado, sem gravar")
    p.add_argument("--login", action="store_true",
                   help="Abre o Chrome e espera você concluir o login no gov.br (não consulta)")
    return p.parse_args(argv)


def config_from_args(args) -> Config:
    sheet_ids = args.spreadsheet_id or [DEFAULT_SPREADSHEET_ID]
    xlsx_files = args.xlsx or [DEFAULT_XLSX]
    out_files = args.out_xlsx or []

    sheet_targets = [{"nome": f"Planilha {i}", "spreadsheet_id": sid}
                     for i, sid in enumerate(sheet_ids, 1)]
    xlsx_targets = []
    for i, xf in enumerate(xlsx_files):
        # Cada --xlsx casa com o --out-xlsx de mesma ordem; sem par, cai no default.
        out = out_files[i] if i < len(out_files) else DEFAULT_OUT_XLSX
        xlsx_targets.append({"nome": Path(xf).stem or f"Planilha {i + 1}",
                             "xlsx": xf, "out_xlsx": out})

    return Config(
        source=args.source,
        credentials=args.credentials,
        spreadsheet_id=sheet_ids[0],
        xlsx=xlsx_files[0],
        out_xlsx=out_files[0] if out_files else DEFAULT_OUT_XLSX,
        sheet_targets=sheet_targets,
        xlsx_targets=xlsx_targets,
        limit=args.limit,
        dry_run=args.dry_run,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        cdp_port=args.cdp_port,
        profile_dir=args.profile_dir or str(profile_path()),
    )


def _ask_login_console() -> bool:
    """Guia o login interativo no terminal. False = usuário desistiu."""
    print("\n" + "-" * 60)
    print("É preciso logar no gov.br. Na janela do Chrome que está aberta:")
    print("  → clique em 'Entrar com o gov.br' e faça login (CPF/senha + 2º fator).")
    print("  → NÃO feche o Chrome.")
    resp = input("Depois que a página de consulta abrir, pressione ENTER (ou 'q' p/ sair): ")
    return resp.strip().lower() != "q"


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = config_from_args(args)

    if args.login:
        ok = runner.login_flow(cfg, log=print, aguardar=_ask_login_console)
        if not ok:
            print("Login não concluído. Encerrando.", file=sys.stderr)
            return 1
        print("Pode rodar as consultas agora (ex.: python main.py --limit 5). "
              "Mantenha a janela do Chrome aberta.")
        return 0

    resultados = runner.run_many(cfg, log=print, ask_login=_ask_login_console)
    if not resultados or not all(r.ok for _, r in resultados):
        for nome, r in resultados:
            if not r.ok and r.mensagem:
                print(f"[{nome or '-'}] {r.mensagem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
