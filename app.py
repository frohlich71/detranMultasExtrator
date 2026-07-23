#!/usr/bin/env python3
"""Ponto de entrada do programa empacotado.

    app.py            → abre a tela
    app.py --run      → execução agendada: sem interação nenhuma, grava log e histórico
    app.py --login    → só o fluxo de login no gov.br, com uma janelinha

O modo `--run` é o que o Agendador de Tarefas / launchd chama (ver `scheduler.py`). Ele
nunca pergunta nada: se a sessão do gov.br tiver expirado, registra o problema, avisa
com uma notificação e sai com código 1.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime

import history
import runner
from config import Config, logs_dir


def _notify(titulo: str, mensagem: str) -> None:
    """Notificação de desktop sem dependência externa; falha em silêncio."""
    try:
        if sys.platform == "darwin":
            subprocess.run([
                "osascript", "-e",
                f'display notification {mensagem!r} with title {titulo!r}',
            ], capture_output=True)
        elif sys.platform == "win32":
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(titulo, mensagem)
            root.destroy()
    except Exception:  # noqa: BLE001 - notificação nunca pode derrubar a execução
        pass


def run_headless() -> int:
    """Execução agendada. Log em arquivo, zero interação."""
    cfg = Config.load()
    log_path = logs_dir() / f"run-{datetime.now():%Y-%m-%d}.log"

    with log_path.open("a", encoding="utf-8") as fh:
        def log(msg: str) -> None:
            fh.write(f"{datetime.now():%H:%M:%S}  {msg}\n")
            fh.flush()

        log("=" * 70)
        log(f"Execução agendada iniciada ({datetime.now():%d/%m/%Y %H:%M:%S})")
        try:
            resultados = runner.run_many(cfg, log=log, ask_login=None)
        except Exception as exc:  # noqa: BLE001
            log(f"✗ Erro inesperado: {exc}")
            resultados = [("", runner.RunResult(mensagem=str(exc)))]

    for nome, result in resultados:
        history.record(result, cfg, origem="agendado", planilha=nome)

    needs_login = any(r.needs_login for _, r in resultados)
    todas_ok = bool(resultados) and all(r.ok for _, r in resultados)

    if needs_login:
        _notify("DetranExtractor",
                "A sessão do gov.br expirou. Abra o programa e faça login de novo.")
    elif not todas_ok:
        falhas = "; ".join(f"{nome or '-'}: {r.mensagem}"
                           for nome, r in resultados if not r.ok)
        _notify("DetranExtractor", f"A consulta falhou: {falhas}")

    return 0 if todas_ok else 1


def login_only() -> int:
    """Abre o Chrome e guia o login, usando só caixas de diálogo."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()

    def aguardar() -> bool:
        return messagebox.askokcancel(
            "Login no gov.br",
            "Faça o login na janela do Chrome que abriu e clique em OK.\n"
            "NÃO feche o Chrome.")

    ok = runner.login_flow(Config.load(), log=lambda m: print(m), aguardar=aguardar)
    messagebox.showinfo("DetranExtractor",
                        "Sessão autenticada." if ok else "Login não concluído.")
    root.destroy()
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DetranExtractor")
    p.add_argument("--run", action="store_true",
                   help="Execução agendada (sem interface, sem perguntas)")
    p.add_argument("--login", action="store_true",
                   help="Só abre o Chrome para o login no gov.br")
    args = p.parse_args(argv)

    if args.run:
        return run_headless()
    if args.login:
        return login_only()

    import gui
    return gui.launch()


if __name__ == "__main__":
    raise SystemExit(main())
