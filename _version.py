"""Versão do programa.

Em desenvolvimento a versão é derivada do git (`git describe`). No executável
empacotado não há git nem histórico, então o build (CI) grava a versão já resolvida
em `_version_baked.txt` e é esse arquivo que passa a valer.

Formato (PEP 440-ish):
    tag exata            v1.2.3            -> 1.2.3
    N commits após a tag v1.2.3-5-g1a2b3c -> 1.2.3.dev5+g1a2b3c
    repo ainda sem tags  1a2b3c            -> 0.0.0+g1a2b3c
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _base_dir() -> Path:
    """Onde procurar o arquivo baked: raiz do bundle quando empacotado, senão o repo."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _HERE))
    return _HERE


def _normalize(desc: str) -> str:
    desc = desc.strip()
    dirty = desc.endswith("-dirty")
    if dirty:
        desc = desc[: -len("-dirty")]
    desc = desc.lstrip("v")
    m = re.match(r"^(\d+\.\d+\.\d+)(?:-(\d+)-g([0-9a-fA-F]+))?$", desc)
    if m:
        base, count, sha = m.groups()
        v = base if not count else f"{base}.dev{count}+g{sha}"
    else:  # repo sem nenhuma tag: describe --always devolve só o sha curto
        v = f"0.0.0+g{desc}" if desc else "0.0.0"
    if dirty:
        v += ".dirty" if "+" in v else "+dirty"
    return v


def _from_git() -> str | None:
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_HERE, capture_output=True, text=True, timeout=5,
        )
    except Exception:  # noqa: BLE001 - sem git, cai no fallback
        return None
    if r.returncode != 0:
        return None
    return _normalize(r.stdout)


def _read_baked() -> str | None:
    try:
        return (_base_dir() / "_version_baked.txt").read_text("utf-8").strip() or None
    except OSError:
        return None


def numeric_tuple(version: str) -> tuple[int, int, int, int]:
    """(major, minor, patch, dev) — para o recurso de versão do Windows / Info.plist."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\.dev(\d+))?", version)
    if not m:
        return (0, 0, 0, 0)
    major, minor, patch, dev = m.groups()
    return (int(major), int(minor), int(patch), int(dev or 0))


def short(version: str) -> str:
    """Só `X.Y.Z` — o que Info.plist/CFBundleShortVersionString aceita."""
    return ".".join(str(n) for n in numeric_tuple(version)[:3])


__version__ = _read_baked() or _from_git() or "0.0.0+unknown"


if __name__ == "__main__":
    print(__version__)
