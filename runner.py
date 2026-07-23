"""Pipeline de consulta, sem depender de console.

Extraído do antigo `main.main()` para poder rodar em três contextos diferentes:

    CLI        `main.py`  → log = print, ask_login = input()
    Tela       `gui.py`   → log = fila da UI, ask_login = janela modal
    Agendado   `app.py --run` → log = arquivo, ask_login = None (nunca pede nada)

Quando `ask_login` é None e a sessão está morta, `run()` devolve
`RunResult(ok=False, needs_login=True)` na hora — é isso que impede uma execução
agendada de travar para sempre esperando alguém digitar ENTER.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

from browser import connect_context, ensure_chrome, port_open
from config import Config
from controle_multas import (
    XlsxSink,
    build_row,
    is_pendente,
    read_existing_ait,
    read_orgao_lookup,
)
from detran_client import CONSULTA_URL, ConsultaResult, consultar_veiculo, is_login_wall
from vehicles_source import read_vehicles


@dataclass
class RunResult:
    ok: bool = False
    novas: int = 0
    veiculos: int = 0
    erros: int = 0
    needs_login: bool = False
    mensagem: str = ""
    cancelado: bool = False
    linhas: list = field(default_factory=list)  # multas novas, para o resumo da tela


def _noop_log(msg: str) -> None:  # pragma: no cover - default inofensivo
    pass


# --------------------------------------------------------------------- carga de dados

def load_data(cfg: Config, log=_noop_log):
    """Carrega veículos + AIs existentes + tabela de órgãos e prepara o sink de escrita.

    Retorna (vehicles, existing_ait, orgao_lookup, sink). `sink` é None em dry-run.
    """
    if cfg.source == "sheets":
        import sheets  # import tardio: só exige gspread quando realmente usar o Sheets

        cred = Path(cfg.credentials)
        if not cred.exists():
            raise FileNotFoundError(cfg.credentials)
        ss = sheets.open_spreadsheet(str(cred), cfg.spreadsheet_id)
        vehicles = sheets.read_vehicles(ss, limit=cfg.limit)
        existing_ait = sheets.read_existing_ait(ss)
        orgao_lookup = sheets.read_orgao_lookup(ss)
        sink = None if cfg.dry_run else sheets.SheetsSink(ss)
        return vehicles, existing_ait, orgao_lookup, sink

    xlsx_path = Path(cfg.xlsx)
    if not xlsx_path.exists():
        raise FileNotFoundError(cfg.xlsx)
    vehicles = read_vehicles(xlsx_path, limit=cfg.limit)
    existing_ait = read_existing_ait(xlsx_path)
    orgao_lookup = read_orgao_lookup(xlsx_path)
    sink = None if cfg.dry_run else XlsxSink(xlsx_path, cfg.out_xlsx)
    return vehicles, existing_ait, orgao_lookup, sink


# --------------------------------------------------------------------- apresentação

def _fmt(value, default="-"):
    if value is None or value == "":
        return default
    return value


def log_result(result: ConsultaResult, log=_noop_log) -> None:
    v = result.vehicle
    header = f"{v.placa}  ·  {v.carro or 'sem nome'}  (renavam {v.renavam})"
    log("")
    log("=" * len(header))
    log(header)
    log("=" * len(header))

    if not result.ok:
        log(f"  ✗ ERRO [{_fmt(result.status)}]: {result.error}")
        return

    infracoes = result.infracoes or []
    if not infracoes:
        log("  ✓ Nenhuma infração encontrada.")
        return

    log(f"  ✓ {len(infracoes)} infração(ões):")
    for i, inf in enumerate(infracoes, 1):
        if not isinstance(inf, dict):
            log(f"    {i}. {inf}")
            continue
        desc = inf.get("descrInfracao") or inf.get("descricao")
        data = inf.get("data")
        hora = inf.get("hora")
        local = inf.get("local")
        data_hora = f"{_fmt(data)} {hora}".strip() if hora else _fmt(data)
        log(f"    {i}. [{_fmt(inf.get('codInfracao'))}] {_fmt(desc)}")
        log(f"       {data_hora}  ·  valor: {_fmt(inf.get('valor'))}  ·  "
            f"pontos: {_fmt(inf.get('pontuacao'))}  ·  situação: {_fmt(inf.get('grupo'))}")
        extras = []
        if inf.get("serieAIT"):
            extras.append(f"AIT {inf['serieAIT']}")
        if inf.get("orgaoFiscalizador"):
            extras.append(inf["orgaoFiscalizador"])
        if extras:
            log(f"       {'  ·  '.join(extras)}")
        if local:
            log(f"       local: {local}")


def processar_multas(result: ConsultaResult, existing_ait: set[str], orgao_lookup,
                     sink, log=_noop_log, coletadas: list | None = None) -> int:
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
        if coletadas is not None:
            coletadas.append(row)
        novas += 1
        log(f"    + NOVA pendente: AIT {row.ait}  ·  {row.data}  ·  "
            f"R$ {row.valor}  ·  {row.orgao}  ·  {row.status}")
    if novas == 0:
        log("    (nenhuma multa pendente nova)")
    return novas


# --------------------------------------------------------------------- navegador/sessão

def _abrir_chrome(cfg: Config, log=_noop_log) -> None:
    reused = port_open(cfg.cdp_port)
    ensure_chrome(cfg.profile_dir, cfg.cdp_port, CONSULTA_URL)
    if reused:
        log(f"Reaproveitando o Chrome já aberto na porta {cfg.cdp_port}.")
    else:
        log(f"Chrome aberto (porta {cfg.cdp_port}). Deixe essa janela aberta.")


def _autenticado(context) -> bool:
    """True se a página de consulta abre sem a parede de login do gov.br."""
    page = context.new_page()
    try:
        page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        return not is_login_wall(page)
    finally:
        page.close()


def login_flow(cfg: Config, log=_noop_log, aguardar=None) -> bool:
    """Abre o Chrome e espera o usuário concluir o login no gov.br.

    `aguardar()` é chamado a cada tentativa e deve bloquear até o usuário sinalizar que
    terminou; devolver False cancela. Sem `aguardar`, só verifica o estado atual.
    """
    _abrir_chrome(cfg, log)
    with sync_playwright() as pw:
        _, context = connect_context(pw, cfg.cdp_port)
        while not _autenticado(context):
            if aguardar is None:
                log("Sessão não autenticada.")
                return False
            log("É preciso logar no gov.br na janela do Chrome que está aberta.")
            if not aguardar():
                return False
        log("✓ Sessão autenticada.")
        return True


# --------------------------------------------------------------------- execução

def _ensure_auth(context, ask_login, log, veiculos: int = 0) -> RunResult | None:
    """Garante a sessão do gov.br. Devolve None se autenticado, ou um RunResult para abortar.

    Preserva o contrato não-bloqueante: `ask_login is None` + sessão morta ⇒ devolve
    `needs_login` na hora, sem esperar ninguém digitar nada.
    """
    if _autenticado(context):
        log("✓ Sessão autenticada.")
        return None
    if ask_login is None:
        msg = ("Sessão do gov.br expirada. Abra o programa e clique em "
               "'Fazer login no gov.br' (e não feche o Chrome).")
        log(f"✗ {msg}")
        return RunResult(veiculos=veiculos, needs_login=True, mensagem=msg)
    while not _autenticado(context):
        if not ask_login():
            return RunResult(veiculos=veiculos, needs_login=True,
                             cancelado=True, mensagem="Login não concluído.")
    log("✓ Sessão autenticada.")
    return None


def _run_one(cfg: Config, context, log=_noop_log, should_stop=None) -> RunResult:
    """Consulta uma planilha num `context` já aberto e autenticado. Nunca levanta exceção."""
    should_stop = should_stop or (lambda: False)

    try:
        vehicles, existing_ait, orgao_lookup, sink = load_data(cfg, log)
    except FileNotFoundError as exc:
        msg = f"Arquivo não encontrado: {exc}"
        log(f"✗ {msg}")
        return RunResult(mensagem=msg)
    except Exception as exc:  # noqa: BLE001 - erro de rede/credencial do Sheets
        msg = f"Falha ao carregar os dados: {exc}"
        log(f"✗ {msg}")
        return RunResult(mensagem=msg)

    if not vehicles:
        msg = "Nenhum veículo válido encontrado na planilha."
        log(f"✗ {msg}")
        return RunResult(mensagem=msg)

    destino = "Google Sheets ao vivo" if cfg.source == "sheets" else f"arquivo {cfg.out_xlsx}"
    log(f"{len(existing_ait)} multas já registradas na aba CONTROLE DE MULTAS.")
    log(f"Fonte/destino: {cfg.source} ({destino})"
        f"{' — DRY-RUN' if cfg.dry_run else ''}.")

    result = RunResult(veiculos=len(vehicles))
    log(f"\nConsultando {len(vehicles)} veículo(s)...")
    for idx, vehicle in enumerate(vehicles):
        if should_stop():
            result.cancelado = True
            log("\n⏹ Interrompido pelo usuário.")
            break

        consulta = consultar_veiculo(context, vehicle)
        log_result(consulta, log)
        if not consulta.ok:
            result.erros += 1
        result.novas += processar_multas(
            consulta, existing_ait, orgao_lookup, sink, log, result.linhas
        )

        if idx < len(vehicles) - 1:
            delay = random.uniform(cfg.min_delay, cfg.max_delay)
            log(f"\n  … aguardando {delay:.1f}s antes do próximo veículo")
            # Dorme em fatias para o botão "Parar" responder rápido.
            fim = time.monotonic() + delay
            while time.monotonic() < fim and not should_stop():
                time.sleep(0.2)

    if sink is not None and result.novas > 0:
        try:
            sink.save()
        except Exception as exc:  # noqa: BLE001
            msg = f"Falha ao gravar o destino: {exc}"
            log(f"✗ {msg}")
            result.mensagem = msg
            return result
        destino = "no Google Sheets" if cfg.source == "sheets" else f"em: {cfg.out_xlsx}"
        log(f"\n✓ {result.novas} multa(s) nova(s) gravada(s) {destino}")
    elif cfg.dry_run:
        log(f"\n(dry-run) {result.novas} multa(s) nova(s) seriam adicionadas.")
    else:
        log("\nNenhuma multa nova pendente para adicionar.")

    result.ok = True
    result.mensagem = f"{result.novas} multa(s) nova(s)"
    return result


def run(cfg: Config, log=_noop_log, ask_login=None, should_stop=None) -> RunResult:
    """Roda a pipeline para UMA planilha. Nunca levanta exceção de fluxo normal."""
    should_stop = should_stop or (lambda: False)

    problemas = cfg.validate()
    if problemas:
        for p in problemas:
            log(f"✗ {p}")
        return RunResult(mensagem="; ".join(problemas))

    _abrir_chrome(cfg, log)
    with sync_playwright() as pw:
        _, context = connect_context(pw, cfg.cdp_port)
        aborto = _ensure_auth(context, ask_login, log)
        if aborto is not None:
            return aborto
        result = _run_one(cfg, context, log, should_stop)

    log("Concluído. (O Chrome continua aberto para as próximas execuções.)")
    return result


def run_many(cfg: Config, log=_noop_log, ask_login=None,
             should_stop=None) -> list[tuple[str, RunResult]]:
    """Roda a fila de planilhas em sequência, reaproveitando a mesma sessão do Chrome.

    Devolve um `(nome, RunResult)` por planilha. Uma planilha que falhe não derruba a fila;
    já uma sessão morta aborta tudo (não adianta seguir sem login).
    """
    should_stop = should_stop or (lambda: False)

    problemas = cfg.validate_targets()
    if problemas:
        for p in problemas:
            log(f"✗ {p}")
        return [("", RunResult(mensagem="; ".join(problemas)))]

    alvos = cfg.iter_target_configs()
    _abrir_chrome(cfg, log)

    resultados: list[tuple[str, RunResult]] = []
    with sync_playwright() as pw:
        _, context = connect_context(pw, cfg.cdp_port)

        aborto = _ensure_auth(context, ask_login, log)
        if aborto is not None:
            return [(nome, RunResult(needs_login=aborto.needs_login,
                                     cancelado=aborto.cancelado,
                                     mensagem=aborto.mensagem))
                    for nome, _ in alvos]

        total = len(alvos)
        for i, (nome, cfg_alvo) in enumerate(alvos, 1):
            if should_stop():
                log(f"\n⏹ Fila interrompida antes de '{nome}'.")
                resultados.append((nome, RunResult(cancelado=True,
                                                   mensagem="Interrompido antes de iniciar.")))
                continue
            cabecalho = f" PLANILHA {i}/{total}: {nome} "
            log("\n" + "#" * 70)
            log(f"#{cabecalho:^68}#")
            log("#" * 70)
            resultados.append((nome, _run_one(cfg_alvo, context, log, should_stop)))

    veiculos = sum(r.veiculos for _, r in resultados)
    novas = sum(r.novas for _, r in resultados)
    ok = sum(1 for _, r in resultados if r.ok)
    log("\n" + "=" * 70)
    log(f"Fila concluída: {ok}/{len(resultados)} planilha(s) OK · "
        f"{veiculos} veículo(s) · {novas} multa(s) nova(s).")
    log("(O Chrome continua aberto para as próximas execuções.)")
    return resultados
