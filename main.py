#!/usr/bin/env python3
"""Extractor de multas DETRAN-RS.

Lê a lista de veículos (xlsx local), consulta as infrações de cada um no site do
DETRAN-RS e imprime o resultado no console.

O site exige login no gov.br e guarda o token no sessionStorage. Por isso o programa
mantém um **Chrome real vivo** (com porta de debug) e se **anexa** a ele via CDP,
reaproveitando a sessão já autenticada. Faça o login uma vez na janela do Chrome que
abrir; enquanto essa janela ficar aberta, as próximas execuções nem pedem login.

Uso:
    python main.py --login          # abre o Chrome e espera você logar no gov.br
    python main.py --limit 5        # consulta os 5 primeiros veículos
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from browser import connect_context, ensure_chrome, port_open
from controle_multas import (
    XlsxSink,
    build_row,
    is_pendente,
    read_existing_ait,
    read_orgao_lookup,
)
from detran_client import CONSULTA_URL, ConsultaResult, consultar_veiculo, is_login_wall
from vehicles_source import read_vehicles

DEFAULT_XLSX = "CONTROLE DE MULTAS VICTOR.xlsx"
DEFAULT_OUT_XLSX = "CONTROLE DE MULTAS VICTOR - TESTE.xlsx"
PROFILE_DIR = ".pw-profile"
DEFAULT_CDP_PORT = 9222


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Consulta multas do DETRAN-RS para uma lista de veículos.")
    p.add_argument("--xlsx", default=DEFAULT_XLSX, help="Caminho do arquivo .xlsx (default: %(default)s)")
    p.add_argument("--limit", type=int, default=5, help="Máximo de veículos a consultar (default: %(default)s)")
    p.add_argument("--min-delay", type=float, default=6.0, help="Espera mínima entre veículos, em segundos")
    p.add_argument("--max-delay", type=float, default=15.0, help="Espera máxima entre veículos, em segundos")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT, help="Porta de debug do Chrome (default: %(default)s)")
    p.add_argument("--source", choices=["xlsx", "sheets"], default="xlsx",
                   help="De onde ler os veículos e onde gravar as multas (default: %(default)s)")
    p.add_argument("--credentials", default="credentials.json",
                   help="JSON da Service Account (apenas para --source sheets)")
    p.add_argument("--out-xlsx", default=DEFAULT_OUT_XLSX,
                   help="Arquivo .xlsx de saída onde as multas novas são gravadas (--source xlsx)")
    p.add_argument("--dry-run", action="store_true",
                   help="Só mostra o que seria adicionado, sem gravar")
    p.add_argument("--login", action="store_true",
                   help="Abre o Chrome e espera você concluir o login no gov.br (não consulta)")
    return p.parse_args(argv)


def _fmt(value, default="-"):
    if value is None or value == "":
        return default
    return value


def print_result(result: ConsultaResult) -> None:
    v = result.vehicle
    header = f"{v.placa}  ·  {v.carro or 'sem nome'}  (renavam {v.renavam})"
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))

    if not result.ok:
        print(f"  ✗ ERRO [{_fmt(result.status)}]: {result.error}")
        return

    infracoes = result.infracoes or []
    if not infracoes:
        print("  ✓ Nenhuma infração encontrada.")
        return

    print(f"  ✓ {len(infracoes)} infração(ões):")
    for i, inf in enumerate(infracoes, 1):
        if isinstance(inf, dict):
            desc = inf.get("descrInfracao") or inf.get("descricao")
            data = inf.get("data")
            hora = inf.get("hora")
            local = inf.get("local")
            valor = inf.get("valor")
            pontos = inf.get("pontuacao")
            codigo = inf.get("codInfracao")
            serie = inf.get("serieAIT")
            situacao = inf.get("grupo")  # Paga / Não Paga / Vencida / ...
            orgao = inf.get("orgaoFiscalizador")
            data_hora = f"{_fmt(data)} {hora}".strip() if hora else _fmt(data)
            print(f"    {i}. [{_fmt(codigo)}] {_fmt(desc)}")
            print(f"       {data_hora}  ·  valor: {_fmt(valor)}  ·  pontos: {_fmt(pontos)}  ·  situação: {_fmt(situacao)}")
            extras = []
            if serie:
                extras.append(f"AIT {serie}")
            if orgao:
                extras.append(orgao)
            if extras:
                print(f"       {'  ·  '.join(extras)}")
            if local:
                print(f"       local: {local}")
        else:
            print(f"    {i}. {inf}")


def ensure_authenticated(context, port: int) -> bool:
    """Garante que a sessão está autenticada; se não, guia o login interativo.

    Retorna True quando autenticado, False se o usuário desistir.
    """
    page = context.new_page()
    try:
        page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        while is_login_wall(page):
            print("\n" + "-" * 60)
            print("É preciso logar no gov.br. Na janela do Chrome que está aberta:")
            print("  → clique em 'Entrar com o gov.br' e faça login (CPF/senha + 2º fator).")
            print("  → NÃO feche o Chrome.")
            resp = input("Depois que a página de consulta abrir, pressione ENTER (ou 'q' p/ sair): ")
            if resp.strip().lower() == "q":
                return False
            page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
        return True
    finally:
        page.close()


def load_data(args):
    """Carrega veículos + AIs existentes + tabela de órgãos e prepara o sink de escrita.

    Retorna (vehicles, existing_ait, orgao_lookup, sink). `sink` é None em --dry-run.
    """
    if args.source == "sheets":
        import sheets  # import tardio: só exige gspread quando realmente usar o Sheets

        cred = Path(args.credentials)
        if not cred.exists():
            raise FileNotFoundError(args.credentials)
        ss = sheets.open_spreadsheet(str(cred))
        vehicles = sheets.read_vehicles(ss, limit=args.limit)
        existing_ait = sheets.read_existing_ait(ss)
        orgao_lookup = sheets.read_orgao_lookup(ss)
        sink = None if args.dry_run else sheets.SheetsSink(ss)
        return vehicles, existing_ait, orgao_lookup, sink

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        raise FileNotFoundError(args.xlsx)
    vehicles = read_vehicles(xlsx_path, limit=args.limit)
    existing_ait = read_existing_ait(xlsx_path)
    orgao_lookup = read_orgao_lookup(xlsx_path)
    sink = None if args.dry_run else XlsxSink(xlsx_path, args.out_xlsx)
    return vehicles, existing_ait, orgao_lookup, sink


def main(argv=None) -> int:
    args = parse_args(argv)

    existing_ait: set[str] = set()
    orgao_lookup: dict[str, str] = {}
    sink = None
    vehicles = []
    if not args.login:
        try:
            vehicles, existing_ait, orgao_lookup, sink = load_data(args)
        except FileNotFoundError as exc:
            print(f"Arquivo não encontrado: {exc}", file=sys.stderr)
            return 1
        if not vehicles:
            print("Nenhum veículo válido encontrado na planilha.", file=sys.stderr)
            return 1
        destino = "Google Sheets ao vivo" if args.source == "sheets" else f"arquivo {args.out_xlsx}"
        print(f"{len(existing_ait)} multas já registradas na aba CONTROLE DE MULTAS.")
        print(f"Fonte/destino: {args.source} ({destino}){' — DRY-RUN' if args.dry_run else ''}.")

    reused = port_open(args.cdp_port)
    ensure_chrome(PROFILE_DIR, args.cdp_port, CONSULTA_URL)
    if reused:
        print(f"Reaproveitando o Chrome já aberto na porta {args.cdp_port}.")
    else:
        print(f"Chrome aberto (porta {args.cdp_port}). Deixe essa janela aberta.")

    with sync_playwright() as pw:
        browser, context = connect_context(pw, args.cdp_port)

        if not ensure_authenticated(context, args.cdp_port):
            print("Login não concluído. Encerrando.", file=sys.stderr)
            return 1
        print("✓ Sessão autenticada.")

        if args.login:
            print("Pode rodar as consultas agora (ex.: python main.py --limit 5). "
                  "Mantenha a janela do Chrome aberta.")
            return 0

        print(f"\nConsultando {len(vehicles)} veículo(s)...")
        total_novas = 0
        for idx, vehicle in enumerate(vehicles):
            result = consultar_veiculo(context, vehicle)
            print_result(result)
            total_novas += processar_multas(result, existing_ait, orgao_lookup, sink)

            if idx < len(vehicles) - 1:
                delay = random.uniform(args.min_delay, args.max_delay)
                print(f"\n  … aguardando {delay:.1f}s antes do próximo veículo")
                time.sleep(delay)

    if sink is not None and total_novas > 0:
        sink.save()
        destino = "no Google Sheets" if args.source == "sheets" else f"em: {args.out_xlsx}"
        print(f"\n✓ {total_novas} multa(s) nova(s) gravada(s) {destino}")
    elif args.dry_run:
        print(f"\n(dry-run) {total_novas} multa(s) nova(s) seriam adicionadas.")
    else:
        print("\nNenhuma multa nova pendente para adicionar.")

    print("Concluído. (O Chrome continua aberto para as próximas execuções.)")
    return 0


def processar_multas(result: ConsultaResult, existing_ait: set[str], orgao_lookup, sink) -> int:
    """Filtra pendentes, deduplica por AIT e envia as novas ao sink. Retorna quantas."""
    if not result.ok or not result.infracoes:
        return 0
    novas = 0
    for inf in result.infracoes:
        if not isinstance(inf, dict) or not is_pendente(inf):
            continue
        ait = str(inf.get("serieAIT") or "").strip()
        if not ait or ait.upper() in existing_ait:
            continue
        existing_ait.add(ait.upper())  # evita duplicar dentro da própria rodada
        row = build_row(result.vehicle.carro, inf, orgao_lookup)
        if sink is not None:
            sink.append(row)
        novas += 1
        print(f"    + NOVA pendente: AIT {row.ait}  ·  {row.data}  ·  R$ {row.valor}  ·  {row.orgao}")
    if novas == 0:
        print("    (nenhuma multa pendente nova)")
    return novas


if __name__ == "__main__":
    raise SystemExit(main())
