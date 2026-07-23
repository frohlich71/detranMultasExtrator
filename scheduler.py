"""Agendamento da execução diária no agendador nativo do sistema.

Windows → Agendador de Tarefas (`schtasks`), macOS/Linux → launchd (LaunchAgent).

**Cuidado que vale para os dois**: a tarefa roda na sessão interativa do usuário, nunca
como serviço/SYSTEM. O Chrome precisa de um desktop com sessão aberta (e logado no
gov.br) para a consulta funcionar — por isso nada de "executar mesmo sem logon".
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

TASK_NAME = "DetranExtractor"
LAUNCH_LABEL = "com.detranextractor.diario"


class SchedulerError(RuntimeError):
    """Falha ao falar com o agendador do sistema; a mensagem já vem em português."""


# --------------------------------------------------------------------- comando alvo

def run_command() -> list[str]:
    """Comando que o agendador deve executar (`app.py --run`).

    Empacotado pelo PyInstaller, `sys.executable` já é o próprio programa. Rodando pelo
    Python, precisamos apontar para o `app.py` ao lado deste arquivo.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run"]
    return [sys.executable, str(Path(__file__).resolve().parent / "app.py"), "--run"]


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def _parse_hhmm(horario: str) -> tuple[int, int]:
    try:
        h, m = horario.strip().split(":")
        hh, mm = int(h), int(m)
    except (ValueError, AttributeError):
        raise SchedulerError(f"Horário inválido: {horario!r}. Use HH:MM (ex.: 08:00).")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise SchedulerError(f"Horário fora do intervalo: {horario!r}. Use HH:MM.")
    return hh, mm


# --------------------------------------------------------------------- Windows

def _schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _win_create(horario: str) -> str:
    hh, mm = _parse_hhmm(horario)
    proc = _schtasks([
        "/Create", "/TN", TASK_NAME,
        "/TR", _quote(run_command()),
        "/SC", "DAILY", "/ST", f"{hh:02d}:{mm:02d}",
        "/F",  # sobrescreve se já existir
    ])
    if proc.returncode != 0:
        raise SchedulerError(
            "Não foi possível criar a tarefa no Agendador do Windows: "
            + (proc.stderr or proc.stdout).strip()
        )
    return f"Tarefa '{TASK_NAME}' agendada para todos os dias às {hh:02d}:{mm:02d}."


def _win_remove() -> str:
    proc = _schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if proc.returncode != 0:
        raise SchedulerError(
            "Não foi possível remover a tarefa: " + (proc.stderr or proc.stdout).strip()
        )
    return "Agendamento removido."


def _win_current() -> str | None:
    proc = _schtasks(["/Query", "/TN", TASK_NAME])
    if proc.returncode != 0:
        return None
    return f"Tarefa '{TASK_NAME}' ativa no Agendador do Windows."


# --------------------------------------------------------------------- macOS / Linux

def _plist_path() -> Path:
    path = Path.home() / "Library" / "LaunchAgents"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{LAUNCH_LABEL}.plist"


def _launchctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _mac_create(horario: str) -> str:
    from config import logs_dir

    hh, mm = _parse_hhmm(horario)
    path = _plist_path()
    plist = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": run_command(),
        "StartCalendarInterval": {"Hour": hh, "Minute": mm},
        "StandardOutPath": str(logs_dir() / "launchd.out.log"),
        "StandardErrorPath": str(logs_dir() / "launchd.err.log"),
        "RunAtLoad": False,
    }
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)

    domain = f"gui/{os.getuid()}"
    _launchctl(["bootout", f"{domain}/{LAUNCH_LABEL}"])  # ignora "não estava carregado"
    proc = _launchctl(["bootstrap", domain, str(path)])
    if proc.returncode != 0:
        raise SchedulerError(
            "Não foi possível registrar o agendamento no launchd: "
            + (proc.stderr or proc.stdout).strip()
        )
    return f"Agendado para todos os dias às {hh:02d}:{mm:02d}."


def _mac_remove() -> str:
    path = _plist_path()
    _launchctl(["bootout", f"gui/{os.getuid()}/{LAUNCH_LABEL}"])
    if path.exists():
        path.unlink()
    return "Agendamento removido."


def _mac_current() -> str | None:
    if not _plist_path().exists():
        return None
    try:
        with _plist_path().open("rb") as fh:
            plist = plistlib.load(fh)
        cal = plist.get("StartCalendarInterval") or {}
        return f"Agendado para todos os dias às {cal.get('Hour', 0):02d}:{cal.get('Minute', 0):02d}."
    except Exception:  # noqa: BLE001
        return "Agendamento presente (não foi possível ler o horário)."


# --------------------------------------------------------------------- API pública

def create(horario: str) -> str:
    """Cria/atualiza o agendamento diário. Devolve a mensagem para mostrar na tela."""
    return _win_create(horario) if sys.platform == "win32" else _mac_create(horario)


def remove() -> str:
    return _win_remove() if sys.platform == "win32" else _mac_remove()


def current_schedule() -> str | None:
    """Descrição do agendamento ativo, ou None se não houver."""
    return _win_current() if sys.platform == "win32" else _mac_current()
