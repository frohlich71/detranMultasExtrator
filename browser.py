"""Gerência do navegador via CDP (Chrome DevTools Protocol).

Por que CDP em vez de o Playwright lançar o navegador?

1. **Captcha do gov.br**: o reCAPTCHA rejeita navegadores com marca de automação
   (`navigator.webdriver = true`), que é o que o Playwright liga ao lançar o Chrome.
   Aqui nós lançamos um Chrome **genuíno** (subprocess, sem flags de automação) — o
   login e o captcha funcionam como num navegador normal.

2. **Token no sessionStorage**: o app do DetranRS guarda o token de acesso no
   `sessionStorage`, que o Chrome descarta ao fechar. Por isso não adianta logar e
   reabrir depois. Mantendo o mesmo Chrome vivo e só **anexando** o Playwright a ele
   (`connect_over_cdp`), a sessão autenticada continua válida entre execuções.

O fluxo: um Chrome real fica rodando com `--remote-debugging-port`; o programa se conecta
a ele para dirigir as consultas na sessão já logada.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Google Chrome não encontrado. Instale o Chrome para continuar.")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def ensure_chrome(user_data_dir: str, port: int, start_url: str, timeout_s: int = 40):
    """Garante um Chrome real ouvindo na porta de debug.

    Se já houver um Chrome na porta (de uma execução anterior), reaproveita e devolve
    `None`. Caso contrário, lança um novo Chrome genuíno (destacado do processo pai) e
    espera a porta abrir, devolvendo o Popen.
    """
    if port_open(port):
        return None

    chrome = find_chrome()
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={os.path.abspath(user_data_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            start_url,
        ],
        start_new_session=True,  # sobrevive ao fim do script → sessão fica viva p/ a próxima run
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_open(port):
            return proc
        time.sleep(0.5)
    raise RuntimeError(
        f"Chrome não abriu a porta de debug {port} em {timeout_s}s. "
        "Feche janelas do Chrome que usem o mesmo profile e tente de novo."
    )


def connect_context(pw, port: int):
    """Anexa o Playwright ao Chrome via CDP e devolve (browser, context) já existentes."""
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context
