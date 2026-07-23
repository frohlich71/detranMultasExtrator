"""Tela de configuração e execução (Tkinter).

Três abas: **Configuração** (origem/destino, credenciais, opções), **Execução** (login,
rodar agora, log ao vivo e agendamento) e **Histórico**.

Regra de ouro do Tkinter respeitada aqui: a consulta roda numa thread separada e **nunca**
toca nos widgets. O `log()` da thread só empurra strings numa `queue.Queue`, e a thread
principal drena essa fila a cada 100ms (`root.after`) para escrever no painel.
"""
from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import history
import runner
import scheduler
from _version import __version__
from config import Config, credentials_path, profile_path

PADDING = 8


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=PADDING)
        self.master.title(f"DetranExtractor {__version__} — consulta de multas")
        self.master.minsize(660, 620)
        self.pack(fill="both", expand=True)

        self.cfg = Config.load()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._stop_flag = threading.Event()

        self._build_vars()
        self._build_ui()
        self._refresh_target_trees()
        self._sync_source_frames()
        self._refresh_schedule_label()
        self._refresh_history()
        self.after(100, self._drain_log)

    # ------------------------------------------------------------------ variáveis

    def _build_vars(self) -> None:
        c = self.cfg
        self.var_source = tk.StringVar(value=c.source)
        self.var_credentials = tk.StringVar(value=c.credentials)
        # Filas de planilhas, editadas na tela e persistidas no Config.
        self.sheet_targets: list[dict] = [dict(t) for t in c.sheet_targets]
        self.xlsx_targets: list[dict] = [dict(t) for t in c.xlsx_targets]
        self.var_limit = tk.StringVar(value=str(c.limit))
        self.var_min_delay = tk.StringVar(value=str(c.min_delay))
        self.var_max_delay = tk.StringVar(value=str(c.max_delay))
        self.var_port = tk.StringVar(value=str(c.cdp_port))
        self.var_dry_run = tk.BooleanVar(value=c.dry_run)
        self.var_schedule_time = tk.StringVar(value=c.schedule_time)
        self.var_status = tk.StringVar(value="Pronto.")

    # ------------------------------------------------------------------ construção

    def _build_ui(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(self._tab_config(nb), text="Configuração")
        nb.add(self._tab_run(nb), text="Execução")
        nb.add(self._tab_history(nb), text="Histórico")

        ttk.Label(self, textvariable=self.var_status, relief="sunken",
                  anchor="w", padding=4).pack(fill="x", pady=(PADDING, 0))

    # ---- aba Configuração

    def _tab_config(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=PADDING)

        origem = ttk.LabelFrame(tab, text="Origem e destino dos dados", padding=PADDING)
        origem.pack(fill="x")
        ttk.Radiobutton(origem, text="Google Sheets (ao vivo)", value="sheets",
                        variable=self.var_source,
                        command=self._sync_source_frames).pack(anchor="w")
        ttk.Radiobutton(origem, text="Arquivo .xlsx local (só para conferência)",
                        value="xlsx", variable=self.var_source,
                        command=self._sync_source_frames).pack(anchor="w")

        # --- Google Sheets
        self.frame_sheets = ttk.LabelFrame(tab, text="Google Sheets", padding=PADDING)
        self.frame_sheets.pack(fill="x", pady=(PADDING, 0))
        self.frame_sheets.columnconfigure(1, weight=1)

        ttk.Label(self.frame_sheets, text="credentials.json:").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.frame_sheets, textvariable=self.var_credentials).grid(
            row=0, column=1, sticky="ew", padx=4)
        ttk.Button(self.frame_sheets, text="Escolher…",
                   command=self._pick_credentials).grid(row=0, column=2)

        ttk.Label(self.frame_sheets, text="Planilhas (rodam em sequência):").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 2))
        self.tree_sheets = ttk.Treeview(self.frame_sheets, columns=("nome", "id"),
                                        show="headings", height=4)
        self.tree_sheets.heading("nome", text="Nome")
        self.tree_sheets.heading("id", text="ID da planilha")
        self.tree_sheets.column("nome", width=160, anchor="w")
        self.tree_sheets.column("id", width=340, anchor="w")
        self.tree_sheets.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.tree_sheets.bind("<Double-1>", lambda _e: self._sheet_edit())

        sheet_btns = ttk.Frame(self.frame_sheets)
        sheet_btns.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(sheet_btns, text="Adicionar", command=self._sheet_add).pack(side="left")
        ttk.Button(sheet_btns, text="Editar", command=self._sheet_edit).pack(side="left", padx=4)
        ttk.Button(sheet_btns, text="Remover", command=self._sheet_remove).pack(side="left")

        ttk.Label(
            self.frame_sheets,
            text="Cada planilha precisa estar compartilhada como Editor com o e-mail da\n"
                 "Service Account (…@….iam.gserviceaccount.com).",
            foreground="#666",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- xlsx
        self.frame_xlsx = ttk.LabelFrame(tab, text="Arquivos .xlsx", padding=PADDING)
        self.frame_xlsx.pack(fill="x", pady=(PADDING, 0))
        self.frame_xlsx.columnconfigure(0, weight=1)

        ttk.Label(self.frame_xlsx, text="Planilhas (rodam em sequência):").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.tree_xlsx = ttk.Treeview(self.frame_xlsx, columns=("nome", "entrada", "saida"),
                                      show="headings", height=4)
        self.tree_xlsx.heading("nome", text="Nome")
        self.tree_xlsx.heading("entrada", text="Entrada")
        self.tree_xlsx.heading("saida", text="Saída")
        self.tree_xlsx.column("nome", width=120, anchor="w")
        self.tree_xlsx.column("entrada", width=190, anchor="w")
        self.tree_xlsx.column("saida", width=190, anchor="w")
        self.tree_xlsx.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.tree_xlsx.bind("<Double-1>", lambda _e: self._xlsx_edit())

        xlsx_btns = ttk.Frame(self.frame_xlsx)
        xlsx_btns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(xlsx_btns, text="Adicionar", command=self._xlsx_add).pack(side="left")
        ttk.Button(xlsx_btns, text="Editar", command=self._xlsx_edit).pack(side="left", padx=4)
        ttk.Button(xlsx_btns, text="Remover", command=self._xlsx_remove).pack(side="left")

        # --- opções de execução
        exec_frame = ttk.LabelFrame(tab, text="Opções de execução", padding=PADDING)
        exec_frame.pack(fill="x", pady=(PADDING, 0))

        linha = ttk.Frame(exec_frame)
        linha.pack(fill="x")
        ttk.Label(linha, text="Veículos por execução:").pack(side="left")
        ttk.Entry(linha, textvariable=self.var_limit, width=6).pack(side="left", padx=(4, 16))
        ttk.Label(linha, text="Espera entre veículos (s):").pack(side="left")
        ttk.Entry(linha, textvariable=self.var_min_delay, width=6).pack(side="left", padx=4)
        ttk.Label(linha, text="a").pack(side="left")
        ttk.Entry(linha, textvariable=self.var_max_delay, width=6).pack(side="left", padx=4)

        linha2 = ttk.Frame(exec_frame)
        linha2.pack(fill="x", pady=(6, 0))
        ttk.Label(linha2, text="Porta de debug do Chrome:").pack(side="left")
        ttk.Entry(linha2, textvariable=self.var_port, width=8).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(linha2, text="Simulação (dry-run): não grava nada",
                        variable=self.var_dry_run).pack(side="left")

        ttk.Label(
            exec_frame,
            text="O site do DETRAN limita extração automatizada. Mantenha lotes pequenos\n"
                 "e a espera entre veículos.",
            foreground="#666",
        ).pack(anchor="w", pady=(6, 0))

        rodape = ttk.Frame(tab)
        rodape.pack(fill="x", pady=(PADDING, 0))
        ttk.Button(rodape, text="Salvar configuração", command=self._save).pack(side="left")
        ttk.Button(rodape, text="Abrir pasta de dados",
                   command=self._open_app_dir).pack(side="left", padx=6)
        return tab

    # ---- aba Execução

    def _tab_run(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=PADDING)

        botoes = ttk.Frame(tab)
        botoes.pack(fill="x")
        self.btn_login = ttk.Button(botoes, text="Fazer login no gov.br", command=self._do_login)
        self.btn_login.pack(side="left")
        self.btn_run = ttk.Button(botoes, text="Consultar agora", command=self._do_run)
        self.btn_run.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(botoes, text="Parar", command=self._do_stop, state="disabled")
        self.btn_stop.pack(side="left")
        ttk.Button(botoes, text="Limpar log", command=self._clear_log).pack(side="right")

        log_frame = ttk.LabelFrame(tab, text="Log", padding=4)
        log_frame.pack(fill="both", expand=True, pady=(PADDING, 0))
        self.txt_log = tk.Text(log_frame, height=18, wrap="none", state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        agenda = ttk.LabelFrame(tab, text="Agendamento automático", padding=PADDING)
        agenda.pack(fill="x", pady=(PADDING, 0))
        linha = ttk.Frame(agenda)
        linha.pack(fill="x")
        ttk.Label(linha, text="Rodar todo dia às (HH:MM):").pack(side="left")
        ttk.Entry(linha, textvariable=self.var_schedule_time, width=8).pack(side="left", padx=4)
        ttk.Button(linha, text="Criar/atualizar", command=self._schedule_create).pack(side="left", padx=4)
        ttk.Button(linha, text="Remover", command=self._schedule_remove).pack(side="left")

        self.lbl_schedule = ttk.Label(agenda, text="", foreground="#666")
        self.lbl_schedule.pack(anchor="w", pady=(6, 0))
        ttk.Label(
            agenda,
            text="Atenção: no horário agendado o computador precisa estar ligado, com o\n"
                 "usuário logado e o Chrome do programa aberto e autenticado no gov.br.\n"
                 "Se a sessão tiver expirado, a execução falha e registra no histórico.",
            foreground="#666",
        ).pack(anchor="w", pady=(4, 0))
        return tab

    # ---- aba Histórico

    def _tab_history(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=PADDING)
        cols = ("quando", "origem", "planilha", "veiculos", "novas", "status")
        self.tree = ttk.Treeview(tab, columns=cols, show="headings", height=16)
        for col, titulo, largura in (
            ("quando", "Quando", 150),
            ("origem", "Origem", 80),
            ("planilha", "Planilha", 140),
            ("veiculos", "Veículos", 70),
            ("novas", "Multas novas", 90),
            ("status", "Status", 200),
        ):
            self.tree.heading(col, text=titulo)
            self.tree.column(col, width=largura, anchor="w")
        self.tree.pack(fill="both", expand=True)
        ttk.Button(tab, text="Atualizar", command=self._refresh_history).pack(
            anchor="w", pady=(PADDING, 0))
        return tab

    # ------------------------------------------------------------------ configuração

    def _sync_source_frames(self) -> None:
        """Deixa visível só o bloco da origem escolhida."""
        sheets = self.var_source.get() == "sheets"
        for frame, ativo in ((self.frame_sheets, sheets), (self.frame_xlsx, not sheets)):
            for child in frame.winfo_children():
                try:
                    child.configure(state="normal" if ativo else "disabled")
                except tk.TclError:
                    pass  # Labels não aceitam state em todos os temas

    def _pick_credentials(self) -> None:
        """Importa o JSON da Service Account para a pasta do app.

        Copiar (em vez de guardar o caminho original) garante que a execução agendada
        continue funcionando mesmo que o arquivo baixado seja movido ou apagado.
        """
        origem = filedialog.askopenfilename(
            title="Escolha o credentials.json da Service Account",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not origem:
            return
        destino = credentials_path()
        try:
            if Path(origem).resolve() != destino.resolve():
                shutil.copy2(origem, destino)
        except OSError as exc:
            messagebox.showerror("Erro", f"Não foi possível copiar o arquivo:\n{exc}")
            return
        self.var_credentials.set(str(destino))
        self._status(f"Credenciais importadas para {destino}.")

    def _pick_file(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Escolha a planilha", filetypes=[("Excel", "*.xlsx"), ("Todos", "*.*")])
        if path:
            var.set(path)

    def _pick_save(self, var: tk.StringVar) -> None:
        path = filedialog.asksaveasfilename(
            title="Arquivo de saída", defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if path:
            var.set(path)

    # ------------------------------------------------------------------ filas de planilhas

    def _refresh_target_trees(self) -> None:
        self.tree_sheets.delete(*self.tree_sheets.get_children())
        for t in self.sheet_targets:
            self.tree_sheets.insert("", "end",
                                    values=(t.get("nome", ""), t.get("spreadsheet_id", "")))
        self.tree_xlsx.delete(*self.tree_xlsx.get_children())
        for t in self.xlsx_targets:
            self.tree_xlsx.insert("", "end", values=(t.get("nome", ""),
                                                     t.get("xlsx", ""), t.get("out_xlsx", "")))

    @staticmethod
    def _selected_index(tree: ttk.Treeview) -> int | None:
        sel = tree.selection()
        return tree.index(sel[0]) if sel else None

    def _sheet_add(self) -> None:
        novo = self._prompt_sheet()
        if novo:
            self.sheet_targets.append(novo)
            self._refresh_target_trees()

    def _sheet_edit(self) -> None:
        idx = self._selected_index(self.tree_sheets)
        if idx is None:
            return
        t = self.sheet_targets[idx]
        novo = self._prompt_sheet(t.get("nome", ""), t.get("spreadsheet_id", ""))
        if novo:
            self.sheet_targets[idx] = novo
            self._refresh_target_trees()

    def _sheet_remove(self) -> None:
        idx = self._selected_index(self.tree_sheets)
        if idx is not None:
            del self.sheet_targets[idx]
            self._refresh_target_trees()

    def _xlsx_add(self) -> None:
        novo = self._prompt_xlsx()
        if novo:
            self.xlsx_targets.append(novo)
            self._refresh_target_trees()

    def _xlsx_edit(self) -> None:
        idx = self._selected_index(self.tree_xlsx)
        if idx is None:
            return
        t = self.xlsx_targets[idx]
        novo = self._prompt_xlsx(t.get("nome", ""), t.get("xlsx", ""), t.get("out_xlsx", ""))
        if novo:
            self.xlsx_targets[idx] = novo
            self._refresh_target_trees()

    def _xlsx_remove(self) -> None:
        idx = self._selected_index(self.tree_xlsx)
        if idx is not None:
            del self.xlsx_targets[idx]
            self._refresh_target_trees()

    def _prompt_sheet(self, nome: str = "", sid: str = "") -> dict | None:
        """Diálogo modal nome + ID de uma planilha do Google Sheets."""
        dlg = tk.Toplevel(self)
        dlg.title("Planilha do Google Sheets")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=PADDING)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        v_nome = tk.StringVar(value=nome)
        v_id = tk.StringVar(value=sid)
        ttk.Label(frm, text="Nome:").grid(row=0, column=0, sticky="w")
        e_nome = ttk.Entry(frm, textvariable=v_nome, width=44)
        e_nome.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(frm, text="ID da planilha:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=v_id, width=44).grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        resultado: dict = {}

        def confirmar() -> None:
            sid_val = v_id.get().strip()
            if not sid_val:
                messagebox.showerror("Planilha", "Informe o ID da planilha.", parent=dlg)
                return
            resultado["nome"] = v_nome.get().strip() or "Planilha"
            resultado["spreadsheet_id"] = sid_val
            dlg.destroy()

        botoes = ttk.Frame(frm)
        botoes.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(botoes, text="OK", command=confirmar).pack(side="left")
        ttk.Button(botoes, text="Cancelar", command=dlg.destroy).pack(side="left", padx=4)

        e_nome.focus_set()
        dlg.grab_set()
        dlg.wait_window()
        return resultado or None

    def _prompt_xlsx(self, nome: str = "", entrada: str = "", saida: str = "") -> dict | None:
        """Diálogo modal nome + arquivo de entrada + arquivo de saída de um .xlsx."""
        dlg = tk.Toplevel(self)
        dlg.title("Planilha .xlsx")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=PADDING)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        v_nome = tk.StringVar(value=nome)
        v_in = tk.StringVar(value=entrada)
        v_out = tk.StringVar(value=saida)

        ttk.Label(frm, text="Nome:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=v_nome, width=40).grid(
            row=0, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(frm, text="Entrada:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=v_in, width=40).grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        ttk.Button(frm, text="Escolher…", command=lambda: self._pick_file(v_in)).grid(row=1, column=2)

        ttk.Label(frm, text="Saída:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm, textvariable=v_out, width=40).grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        ttk.Button(frm, text="Escolher…", command=lambda: self._pick_save(v_out)).grid(row=2, column=2)

        resultado: dict = {}

        def confirmar() -> None:
            entrada_val = v_in.get().strip()
            if not entrada_val:
                messagebox.showerror("Planilha", "Escolha o arquivo .xlsx de entrada.", parent=dlg)
                return
            resultado["nome"] = v_nome.get().strip() or (Path(entrada_val).stem or "Planilha")
            resultado["xlsx"] = entrada_val
            resultado["out_xlsx"] = v_out.get().strip()
            dlg.destroy()

        botoes = ttk.Frame(frm)
        botoes.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(botoes, text="OK", command=confirmar).pack(side="left")
        ttk.Button(botoes, text="Cancelar", command=dlg.destroy).pack(side="left", padx=4)

        dlg.grab_set()
        dlg.wait_window()
        return resultado or None

    def _collect(self) -> Config | None:
        """Lê a tela para um Config. None se algum número estiver inválido."""
        try:
            cfg = Config(
                source=self.var_source.get(),
                credentials=self.var_credentials.get().strip(),
                spreadsheet_id=(self.sheet_targets[0]["spreadsheet_id"]
                                if self.sheet_targets else ""),
                xlsx=(self.xlsx_targets[0]["xlsx"] if self.xlsx_targets else ""),
                out_xlsx=(self.xlsx_targets[0]["out_xlsx"] if self.xlsx_targets else ""),
                sheet_targets=[dict(t) for t in self.sheet_targets],
                xlsx_targets=[dict(t) for t in self.xlsx_targets],
                limit=int(self.var_limit.get()),
                dry_run=self.var_dry_run.get(),
                min_delay=float(self.var_min_delay.get()),
                max_delay=float(self.var_max_delay.get()),
                cdp_port=int(self.var_port.get()),
                profile_dir=self.cfg.profile_dir or str(profile_path()),
                schedule_time=self.var_schedule_time.get().strip(),
            )
        except ValueError:
            messagebox.showerror(
                "Configuração inválida",
                "Veículos, esperas e porta precisam ser números.")
            return None
        return cfg

    def _save(self, silencioso: bool = False) -> Config | None:
        cfg = self._collect()
        if cfg is None:
            return None
        self.cfg = cfg
        path = cfg.save()
        if not silencioso:
            problemas = cfg.validate_targets()
            if problemas:
                messagebox.showwarning(
                    "Salvo, mas com pendências", "\n".join(f"• {p}" for p in problemas))
            self._status(f"Configuração salva em {path}.")
        return cfg

    def _open_app_dir(self) -> None:
        import subprocess
        import sys

        from config import app_dir

        path = str(app_dir())
        if sys.platform == "win32":
            subprocess.run(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])

    # ------------------------------------------------------------------ log

    def _log(self, msg: str) -> None:
        """Chamado da thread de trabalho — só enfileira, nunca toca a UI."""
        self.log_queue.put(str(msg))

    def _drain_log(self) -> None:
        linhas = []
        try:
            while True:
                linhas.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if linhas:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", "\n".join(linhas) + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.after(100, self._drain_log)

    def _clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _status(self, msg: str) -> None:
        self.var_status.set(msg)

    # ------------------------------------------------------------------ execução

    def _busy(self, ativo: bool) -> None:
        estado = "disabled" if ativo else "normal"
        self.btn_run.configure(state=estado)
        self.btn_login.configure(state=estado)
        self.btn_stop.configure(state="normal" if ativo else "disabled")

    def _ask_login(self) -> bool:
        """Callback chamado pela thread de trabalho: bloqueia até o usuário responder.

        A caixa de diálogo é criada na thread principal via `after`; a thread de trabalho
        espera no Event.
        """
        evento = threading.Event()
        resposta = {"ok": False}

        def perguntar():
            resposta["ok"] = messagebox.askokcancel(
                "Login no gov.br",
                "Na janela do Chrome que abriu:\n\n"
                "  1. clique em 'Entrar com o gov.br';\n"
                "  2. faça o login (CPF/senha + 2º fator);\n"
                "  3. NÃO feche o Chrome.\n\n"
                "Quando a página de consulta abrir, clique em OK.",
            )
            evento.set()

        self.after(0, perguntar)
        evento.wait()
        return resposta["ok"]

    def _do_run(self) -> None:
        cfg = self._save(silencioso=True)
        if cfg is None:
            return
        problemas = cfg.validate_targets()
        if problemas:
            messagebox.showerror("Configuração incompleta",
                                 "\n".join(f"• {p}" for p in problemas))
            return

        self._stop_flag.clear()
        self._busy(True)
        self._status("Consultando…")

        def trabalho():
            try:
                resultados = runner.run_many(cfg, log=self._log, ask_login=self._ask_login,
                                             should_stop=self._stop_flag.is_set)
                for nome, r in resultados:
                    history.record(r, cfg, origem="manual", planilha=nome)
            except Exception as exc:  # noqa: BLE001 - nada pode matar a thread silenciosamente
                self._log(f"✗ Erro inesperado: {exc}")
                resultados = [("", runner.RunResult(mensagem=str(exc)))]
            self.after(0, lambda: self._finalizar(resultados))

        self.worker = threading.Thread(target=trabalho, daemon=True)
        self.worker.start()

    def _finalizar(self, resultados: list) -> None:
        self._busy(False)
        self._refresh_history()

        veiculos = sum(r.veiculos for _, r in resultados)
        novas = sum(r.novas for _, r in resultados)
        needs_login = next((r for _, r in resultados if r.needs_login), None)
        cancelado = any(r.cancelado for _, r in resultados)
        todas_ok = bool(resultados) and all(r.ok for _, r in resultados)

        if needs_login is not None:
            self._status("Falhou: sessão do gov.br expirada.")
            messagebox.showwarning("Sessão expirada", needs_login.mensagem)
        elif cancelado:
            self._status(f"Interrompido — {novas} multa(s) nova(s) em {veiculos} veículo(s).")
        elif todas_ok:
            self._status(f"Concluído — {novas} multa(s) nova(s) em {veiculos} veículo(s), "
                         f"{len(resultados)} planilha(s).")
        else:
            falhas = "; ".join(f"{nome or '-'}: {r.mensagem}"
                               for nome, r in resultados if not r.ok)
            self._status(f"Falhou: {falhas}")

    def _do_stop(self) -> None:
        self._stop_flag.set()
        self._status("Parando após o veículo atual…")

    def _do_login(self) -> None:
        cfg = self._save(silencioso=True)
        if cfg is None:
            return
        self._busy(True)
        self._status("Aguardando login no gov.br…")

        def trabalho():
            try:
                ok = runner.login_flow(cfg, log=self._log, aguardar=self._ask_login)
            except Exception as exc:  # noqa: BLE001
                self._log(f"✗ Erro ao abrir o Chrome: {exc}")
                ok = False
            self.after(0, lambda: (self._busy(False),
                                   self._status("✓ Sessão autenticada." if ok
                                                else "Login não concluído.")))

        threading.Thread(target=trabalho, daemon=True).start()

    # ------------------------------------------------------------------ agendamento

    def _refresh_schedule_label(self) -> None:
        try:
            atual = scheduler.current_schedule()
        except Exception as exc:  # noqa: BLE001
            atual = f"(não foi possível consultar: {exc})"
        self.lbl_schedule.configure(text=atual or "Nenhum agendamento ativo.")

    def _schedule_create(self) -> None:
        cfg = self._save(silencioso=True)
        if cfg is None:
            return
        try:
            msg = scheduler.create(cfg.schedule_time)
        except scheduler.SchedulerError as exc:
            messagebox.showerror("Agendamento", str(exc))
            return
        self._status(msg)
        self._refresh_schedule_label()

    def _schedule_remove(self) -> None:
        try:
            msg = scheduler.remove()
        except scheduler.SchedulerError as exc:
            messagebox.showerror("Agendamento", str(exc))
            return
        self._status(msg)
        self._refresh_schedule_label()

    # ------------------------------------------------------------------ histórico

    def _refresh_history(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for e in history.read_recent(50):
            if e.get("cancelado"):
                status = "Interrompido"
            elif e.get("ok"):
                status = "OK" + (" (simulação)" if e.get("dry_run") else "")
            else:
                status = e.get("mensagem") or "Falhou"
            self.tree.insert("", "end", values=(
                e.get("quando", "").replace("T", " "),
                e.get("origem", ""),
                e.get("planilha", ""),
                e.get("veiculos", 0),
                e.get("novas", 0),
                status,
            ))


def launch() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
