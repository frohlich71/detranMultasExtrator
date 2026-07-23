# -*- mode: python ; coding: utf-8 -*-
"""Build do executável (PyInstaller).

    pyinstaller detranExtractor.spec

Nota importante: como o programa **nunca lança** um navegador pelo Playwright (só se
anexa a um Chrome real via CDP — ver `browser.py`), os navegadores do Playwright
(`playwright install chromium`, ~150MB) **não** precisam ser embutidos. Só o driver Node
que vem dentro do pacote `playwright`, e é isso que `collect_all` recolhe.

O usuário final precisa ter o Google Chrome instalado — nada mais.

Windows sai como arquivo único (`dist\\DetranExtractor.exe`); macOS sai como `.app`
(onedir), porque um bundle não pode ser um arquivo só — o PyInstaller marcou a combinação
onefile+`.app` como depreciada.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

from _version import __version__, numeric_tuple, short

playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

# Grava a versão resolvida num arquivo que vai embutido, para o app saber a própria
# versão em runtime (não há git dentro do executável). No CI o arquivo já existe; num
# build local ele é gerado aqui a partir do git.
_baked = os.path.join(os.path.dirname(os.path.abspath(SPEC)), "_version_baked.txt")
with open(_baked, "w", encoding="utf-8") as _fh:
    _fh.write(__version__)

# Recurso de versão do Windows (propriedades do .exe: botão direito → Detalhes).
_win_version_file = None
if sys.platform == "win32":
    _vt = numeric_tuple(__version__)
    _win_version_file = os.path.join(os.path.dirname(_baked), "_win_version.txt")
    with open(_win_version_file, "w", encoding="utf-8") as _fh:
        _fh.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_vt}, prodvers={_vt}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'DetranExtractor'),
      StringStruct('FileDescription', 'DetranExtractor'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductName', 'DetranExtractor'),
      StringStruct('ProductVersion', '{__version__}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=playwright_binaries,
    datas=[*playwright_datas, (_baked, ".")],
    hiddenimports=[
        *playwright_hidden,
        "gspread",
        "google.oauth2.service_account",
        "openpyxl",
        "sheets",
        "_version",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

_onefile = sys.platform != "darwin"
_exe_extra = [a.binaries, a.datas, []] if _onefile else [[]]

exe = EXE(
    pyz,
    a.scripts,
    *_exe_extra,
    exclude_binaries=not _onefile,
    name="DetranExtractor",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # app de janela; o modo --run escreve em arquivo de log
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_win_version_file,   # recurso de versão do Windows (None nas outras plataformas)
)

if sys.platform == "darwin":
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="DetranExtractor",
    )
    app = BUNDLE(
        coll,
        name="DetranExtractor.app",
        icon=None,
        bundle_identifier="com.detranextractor.app",
        version=short(__version__),
        info_plist={
            "CFBundleName": "DetranExtractor",
            "CFBundleDisplayName": "DetranExtractor",
            "CFBundleShortVersionString": short(__version__),   # ex: 1.2.3
            "CFBundleVersion": __version__,                     # versão completa (dev+sha)
            "NSHighResolutionCapable": True,
        },
    )
