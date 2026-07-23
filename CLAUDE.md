# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A desktop program (Tkinter GUI + CLI) that queries traffic fines (multas/infrações) for a
fleet of vehicles on the DETRAN-RS public consultation site, then appends the new unpaid
ones to a spreadsheet (local `.xlsx` or live Google Sheets). Ships as a PyInstaller
executable for Windows and macOS, and can schedule itself as a daily task. Docs and
user-facing strings are in Portuguese; keep them that way.

No test suite, no linter config.

## Commands

The venv lives at `.venv/` (Python 3.14). Prefer `./.venv/bin/python` over bare `python`.

Tkinter is required for the GUI; the Homebrew Python needs `brew install python-tk@3.14`.

```bash
./.venv/bin/pip install -r requirements.txt

./.venv/bin/python app.py                   # the GUI
./.venv/bin/python app.py --run             # scheduled mode: no UI, no prompts, exits 0/1
./.venv/bin/python main.py --login          # CLI: opens Chrome, waits for the gov.br login
./.venv/bin/python main.py --limit 5        # query 5 vehicles from the local xlsx
./.venv/bin/python main.py --source sheets --limit 5 --dry-run   # live Sheets, no writes
# queue several spreadsheets in one run (--spreadsheet-id / --xlsx are repeatable):
./.venv/bin/python main.py --source sheets --dry-run --spreadsheet-id ID_A --spreadsheet-id ID_B

./.venv/bin/pyinstaller --noconfirm detranExtractor.spec         # build → dist/
```

`playwright install chromium` is **not** needed: the code only ever attaches over CDP to a
system Chrome, so only the Node driver inside the `playwright` package matters.

Sanity check after edits (there are no tests):

```bash
./.venv/bin/python -c "import app, gui, runner, config, scheduler, history, main, sheets, controle_multas, vehicles_source, detran_client, browser; print('imports OK')"
```

## Architecture

Pipeline per spreadsheet, in `runner._run_one()`: **load data → per-vehicle query → filter/dedup → sink**.
`runner.run()` wraps it for a single spreadsheet; `runner.run_many()` runs a **queue** of
spreadsheets in sequence, opening Chrome and authenticating **once** and reusing the same
CDP session for the whole queue.

- `runner.py` is UI-neutral: it takes `log(msg)` and `ask_login()` callbacks. `main.py` (CLI)
  passes `print`/`input`, `gui.py` passes a `queue.Queue` and a dialog, and `app.py --run`
  passes **`ask_login=None`** — which makes a dead session return
  `RunResult(needs_login=True)` instead of blocking forever. That non-blocking contract is
  the whole reason scheduled runs are safe; don't add an `input()` anywhere below `runner`.
  All three callers use `run_many`, which returns one `(nome, RunResult)` **per spreadsheet**;
  a single failing spreadsheet doesn't abort the queue, but a dead session aborts all of it.
  Auth lives in `_ensure_auth`, the per-spreadsheet body in `_run_one` — both shared by
  `run` and `run_many`.
- `config.py` — `Config` dataclass persisted as JSON in the user's app dir (`app_dir()`:
  `%APPDATA%` / `~/Library/Application Support` / `~/.config`). Everything lives there —
  config, the imported `credentials.json`, the Chrome profile, logs, history — because a
  scheduled run has an unpredictable cwd. Field names match the old argparse names on
  purpose, so `runner.load_data` reads the same attributes it always did. The queue lives in
  `sheet_targets` / `xlsx_targets` (one list per source; each item is a `dict`). `load()`
  migrates old single-spreadsheet configs by seeding the list from the legacy `spreadsheet_id`
  / `xlsx` fields, and `iter_target_configs()` yields one `(nome, Config)` per queued
  spreadsheet by `dataclasses.replace`-ing only the location — so `load_data` stays untouched.
  Global options (credentials, limit, delays, dry-run, port) are shared across the queue.
  `validate()` checks a single spreadsheet; `validate_targets()` checks the whole queue.
- `gui.py` — the worker thread **never** touches widgets: `log()` only enqueues, and the main
  thread drains via `root.after(100, ...)`. `ask_login` from the worker uses a
  `threading.Event` + `after(0, ...)` to bounce the dialog onto the main thread.
- `scheduler.py` — `schtasks` on Windows, a LaunchAgent on macOS. Always in the user's
  interactive session, never as a service/SYSTEM: Chrome needs a desktop.
- `browser.py` — the crux of the design. Playwright does **not** launch the browser. A genuine
  system Chrome is spawned via `subprocess.Popen` with `--remote-debugging-port`, detached
  (`start_new_session` on POSIX, `DETACHED_PROCESS` on Windows — see `_detach_kwargs`), and
  Playwright *attaches* over CDP (`connect_over_cdp`). Two reasons, both hard requirements:
  1. gov.br's reCAPTCHA rejects `navigator.webdriver = true`, which Playwright-launched browsers set.
  2. The Angular SPA keeps its access token in `sessionStorage`, wiped on browser close — so the
     Chrome process must survive between runs. Never "fix" this by switching to `pw.chromium.launch()`.
- `detran_client.py` — one vehicle per fresh page: navigate to the pre-filled consultation URL,
  force the "Todas" filter, click "Consultar", and intercept the XHR the app itself fires
  (`/infracoes/veiculos/publicas/{PLACA}?renavam=...`). Never call the REST endpoint directly —
  the captcha tokens are generated by page JS. Errors are returned as `ConsultaResult(ok=False)`,
  not raised.
- `vehicles_source.py` / `controle_multas.py` — parsing and business rules. Each has a
  `*_from_rows()` function that works on generic row sequences, plus a thin xlsx reader on top;
  `sheets.py` reuses the same `*_from_rows()` against Google Sheets values. **Put new parsing logic
  in the `_from_rows` layer** so both backends get it.
- Sinks (`controle_multas.XlsxSink`, `sheets.SheetsSink`) share an `append(MultaRow)` / `save()`
  interface. They buffer and write once at the end; dry-run passes `sink=None`.
- `sheets.py` is imported lazily inside `runner.load_data` so the xlsx path never needs gspread.

## Domain rules

- Spreadsheet tabs: `LISTA DE CARROS` (A=CARRO, B=PLACA, C=RENAVAM), `CONTROLE DE MULTAS`
  (A=TRELLO, B=CARRO, C=DATA, D=NÚMERO DO AI, E=ORGÃO AUTUADOR, F=VALOR), `ORGÃO AUTUADOR`.
- "Pendente" = `grupo` contains "não paga" / "vencer" / "vencid". Paid, annulled, and blank-grupo
  fines are skipped.
- Dedup key is `serieAIT` (column D), uppercased. `runner.processar_multas` also adds each AIT to
  the in-memory set so a run can't duplicate within itself.
- RENAVAM arrives from xlsx as a float in scientific notation; `int(float(...))` recovers it.
- Órgão is rendered as `000100 - PRF` via the lookup built from the `ORGÃO AUTUADOR` tab.
- Spreadsheets run as a **queue**: `Config.sheet_targets` (Google Sheets) or `xlsx_targets`
  (local files), all sharing one Service Account and one set of execution options. The default
  ID lives in `config.DEFAULT_SPREADSHEET_ID` and seeds the queue for fresh/legacy configs. The
  GUI **copies** the chosen Service Account key into the app dir so a scheduled run doesn't
  depend on where the download landed, and records **one history row per spreadsheet** (the
  `planilha` field).

## Operational cautions

- The DETRAN site restricts bulk automated extraction. Keep the randomized `--min-delay`/`--max-delay`
  throttle and small batches; don't raise concurrency or remove the sleep.
- `--source xlsx` writes through openpyxl, which drops threaded comments and embedded drawings —
  that path is for inspection only. The authoritative write is `--source sheets`.
- A scheduled run only works if the machine is on, the user is logged in, and the program's
  Chrome window is open and authenticated at gov.br. There is no way around this: the token
  is in `sessionStorage`. Failing loudly (log + history + desktop notification + exit 1) is
  the intended behaviour, not a bug to fix.
