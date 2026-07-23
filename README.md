<h1 align="center">detranExtractor</h1>

<p align="center">
  Consulta automatizada das multas (infrações) de uma frota de veículos no <b>DETRAN-RS</b>,
  com gravação das multas novas em planilha <code>.xlsx</code> ou <b>Google Sheets</b>.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Playwright" src="https://img.shields.io/badge/playwright-CDP%20attach-2EAD33">
  <img alt="Plataformas" src="https://img.shields.io/badge/OS-macOS%20%7C%20Linux-lightgrey">
</p>

---

O programa lê uma lista de veículos (placa + renavam) de uma planilha, consulta a
**consulta pública** do site do DETRAN-RS usando um navegador real, filtra as multas
**pendentes** e **acrescenta apenas as que ainda não estão** na sua planilha de controle.

## Índice

- [Recursos](#recursos)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Programa com tela (sem terminal)](#programa-com-tela-sem-terminal)
- [Gerar o executável](#gerar-o-executável)
- [Formato da planilha](#formato-da-planilha)
- [Uso rápido](#uso-rápido)
- [Opções da CLI](#opções-da-cli)
- [Google Sheets ao vivo](#google-sheets-ao-vivo)
- [Regras de negócio](#regras-de-negócio)
- [Solução de problemas](#solução-de-problemas)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Contribuindo](#contribuindo)
- [Aviso legal](#aviso-legal)
- [Licença](#licença)

## Recursos

- 🔎 Consulta placa + renavam na consulta pública do DETRAN-RS (todas as situações).
- 🔐 Login gov.br feito **por você**, num Chrome de verdade — sem burlar captcha.
- 🧾 Filtra só multas **pendentes** (a vencer / vencida / não paga).
- ♻️ **Deduplicação** pelo número do AI: rodar duas vezes não duplica linhas.
- 📤 Dois destinos: arquivo `.xlsx` local (conferência) ou **Google Sheets ao vivo**.
- 🗂️ **Fila de planilhas**: várias planilhas rodam em sequência, reaproveitando a mesma sessão.
- 🧪 `--dry-run` para ver o que seria gravado sem escrever nada.
- 🐢 Throttle randômico entre veículos, para um uso comedido do site.

## Como funciona

O site `pcsdetran.rs.gov.br` é um SPA Angular e **exige login no gov.br** (Central de
Serviços do DetranRS). A consulta de infrações é por placa + renavam, mas só depois de
autenticado. Dois obstáculos e como o programa os resolve:

- **Captcha do gov.br** — o reCAPTCHA da tela de login rejeita navegadores com marca de
  automação (`navigator.webdriver = true`, que o Playwright liga ao lançar o navegador).
  Por isso o programa **não lança o navegador com automação**: ele abre um **Google Chrome
  genuíno** (com porta de debug) e apenas se **anexa** a ele via CDP. O login acontece como
  num navegador normal — e é você quem faz.
- **Token no `sessionStorage`** — o app guarda o token de acesso no `sessionStorage`, que o
  Chrome apaga ao fechar. Por isso o mesmo Chrome fica **vivo** entre execuções: enquanto a
  janela estiver aberta, a sessão segue válida e não é preciso logar de novo.

Com a sessão ativa, o programa navega para a consulta de cada veículo e intercepta o XHR
que o próprio app dispara:

```
GET https://pcsdetran.procergs.com.br/pcsdetran/rest/infracoes/veiculos/publicas/{PLACA}?renavam={RENAVAM}
```

> O endpoint REST **não** é chamado diretamente: os tokens de captcha são gerados pelo
> JavaScript da página.

## Requisitos

- **Python 3.11+** (desenvolvido em 3.14).
- **Google Chrome** instalado (Windows: `C:\Program Files\Google\Chrome\...`; macOS:
  `/Applications/Google Chrome.app`; Linux: `google-chrome` / `chromium` no `PATH`).
- Para a tela: **Tkinter** (vem com o Python oficial; no Python do Homebrew, instale
  `brew install python-tk`).
- Uma conta **gov.br** com acesso à Central de Serviços do DetranRS.
- Opcional: projeto no Google Cloud com **Google Sheets API**, para o modo Sheets.

## Instalação

```bash
git clone https://github.com/<seu-usuario>/detranExtractor.git
cd detranExtractor

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium         # baixa os drivers do Playwright
```

> O `playwright install chromium` instala apenas os drivers. O navegador usado na prática
> é o **Chrome do sistema** — por isso, no executável empacotado, esse passo nem é
> necessário.

## Programa com tela (sem terminal)

Além da CLI, há um aplicativo de janela (Tkinter) que guarda a configuração e sabe se
agendar sozinho:

```bash
python app.py            # abre a tela
python app.py --run      # execução agendada: sem interface, sem perguntas
python app.py --login    # só o login no gov.br
```

A tela tem três abas:

- **Configuração** — origem/destino (Google Sheets ou `.xlsx`), botão para importar o
  `credentials.json`, a **lista de planilhas** (com *Adicionar / Editar / Remover*) que rodam
  em sequência, limite de veículos, esperas, porta do Chrome e o modo simulação.
- **Execução** — botões *Fazer login*, *Consultar agora* e *Parar*, log ao vivo e o
  bloco de agendamento diário. *Parar* interrompe entre uma planilha e a próxima.
- **Histórico** — as últimas execuções (quando, origem, planilha, veículos, multas novas,
  status), com **uma linha por planilha** da fila.

Tudo fica em uma pasta do usuário, **não** no diretório do projeto (uma execução agendada
roda com o diretório de trabalho imprevisível):

| SO | pasta |
|----|-------|
| Windows | `%APPDATA%\DetranExtractor` |
| macOS | `~/Library/Application Support/DetranExtractor` |
| Linux | `~/.config/DetranExtractor` |

Dentro dela: `config.json`, o `credentials.json` importado, `chrome-profile/` (o profile
do Chrome que mantém a sessão), `logs/` e `history.jsonl`. O botão **Abrir pasta de
dados** leva direto até lá.

### Agendamento automático

No bloco *Agendamento* da aba Execução, escolha o horário e clique em
**Criar/atualizar**. O programa registra a tarefa no agendador nativo:

- **Windows** — Agendador de Tarefas (`schtasks`), tarefa `DetranExtractor`, diária.
- **macOS/Linux** — LaunchAgent `com.detranextractor.diario` em `~/Library/LaunchAgents`.

> ⚠️ **A pegadinha do agendamento.** O token do DetranRS vive no `sessionStorage` de um
> Chrome aberto. Então, no horário marcado, é preciso que o **computador esteja ligado**,
> com o **usuário logado** e o **Chrome do programa aberto e autenticado no gov.br**. Por
> isso a tarefa roda na sessão interativa do usuário — nunca como serviço/SYSTEM.
>
> Se a sessão tiver expirado, a execução **não trava esperando login**: ela registra o
> erro em `logs/run-AAAA-MM-DD.log`, grava a falha no histórico, mostra uma notificação e
> sai com código 1. Aí é só abrir o programa e clicar em *Fazer login no gov.br*.

## Gerar o executável

```bash
pip install pyinstaller
pyinstaller detranExtractor.spec
```

Sai em `dist/`: `DetranExtractor.exe` no Windows, `DetranExtractor.app` no macOS. Na
máquina de destino basta ter o **Google Chrome** instalado — nem Python, nem venv, nem
os navegadores do Playwright (o programa só se **anexa** a um Chrome real via CDP, então
o `playwright install chromium` é dispensável no pacote; só o driver Node vai junto).

## Formato da planilha

O arquivo `.xlsx` (ou a planilha do Google) precisa das abas:

**`LISTA DE CARROS`** — a frota:

| A | B | C |
|---|---|---|
| CARRO | PLACA | RENAVAM |
| Gol 2019 | ABC1D23 | 01234567890 |

**`CONTROLE DE MULTAS`** — o destino das multas novas:

| A | B | C | D | E | F |
|---|---|---|---|---|---|
| TRELLO | CARRO | DATA | NÚMERO DO AI | ORGÃO AUTUADOR | VALOR |

**`ORGÃO AUTUADOR`** — de/para do código do órgão para texto (ex.: `000100` → `PRF`).

A primeira linha de cada aba é o cabeçalho.

## Uso rápido

**1) Logar no gov.br** (abre o Chrome; faça o login e volte ao terminal):

```bash
python main.py --login
```

Clique em **"Entrar com o gov.br"**, faça o login (CPF/senha + 2º fator) e, quando a página
de consulta aparecer, pressione **ENTER** no terminal.
**Não feche essa janela do Chrome** — é ela que mantém a sessão viva.

**2) Consultar os veículos** (com o Chrome ainda aberto):

```bash
# conferência, sem gravar nada
python main.py --xlsx "minha-planilha.xlsx" --limit 5 --dry-run

# grava as multas novas num .xlsx de saída
python main.py --xlsx "minha-planilha.xlsx" --limit 10 --min-delay 6 --max-delay 15

# várias planilhas em sequência (repita --xlsx; pareie os --out-xlsx pela ordem)
python main.py --xlsx "frota-a.xlsx" --out-xlsx "saida-a.xlsx" \
               --xlsx "frota-b.xlsx" --out-xlsx "saida-b.xlsx"
```

Enquanto a janela do Chrome continuar aberta, os próximos comandos nem pedem login.
Se você fechar o Chrome, rode `python main.py --login` de novo.

## Opções da CLI

| flag | default | descrição |
|------|---------|-----------|
| `--login` | — | abre o Chrome e espera você logar no gov.br (não consulta) |
| `--source` | `xlsx` | fonte/destino dos dados: `xlsx` ou `sheets` |
| `--xlsx` | `CONTROLE DE MULTAS VICTOR.xlsx` | planilha de entrada; **repita** para enfileirar várias |
| `--out-xlsx` | `CONTROLE DE MULTAS VICTOR - TESTE.xlsx` | arquivo de saída (modo `xlsx`); pareado por ordem com cada `--xlsx` |
| `--spreadsheet-id` | `DEFAULT_SPREADSHEET_ID` | ID da planilha (modo `sheets`); **repita** para enfileirar várias |
| `--credentials` | `credentials.json` | chave da Service Account (modo `sheets`) |
| `--limit` | `5` | máximo de veículos a consultar |
| `--dry-run` | — | só mostra o que seria adicionado, sem gravar |
| `--min-delay` / `--max-delay` | `6` / `15` | throttle (segundos) entre veículos |
| `--cdp-port` | `9222` | porta de debug do Chrome |

> Os defaults de `--xlsx` / `--out-xlsx` apontam para a planilha original do autor.
> Passe sempre o caminho da sua, ou ajuste `DEFAULT_XLSX` em `main.py`.

## Google Sheets ao vivo

Lê os veículos e grava as multas novas **direto na planilha online**, via Service Account
(sem login do Google interativo).

**Setup (uma vez):**

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/) e crie um projeto.
2. Em **APIs e serviços → Biblioteca**, habilite a **Google Sheets API**.
3. Em **APIs e serviços → Credenciais → Criar credenciais → Conta de serviço**: crie a
   conta e, nela, **Chaves → Adicionar chave → JSON**. Salve o arquivo como
   `credentials.json` na raiz do projeto (já está no `.gitignore`).
4. Copie o e-mail da conta de serviço (`nome@projeto.iam.gserviceaccount.com`) e
   **compartilhe sua planilha como _Editor_** com esse e-mail.
5. Ajuste `DEFAULT_SPREADSHEET_ID` em `sheets.py` para o ID da **sua** planilha
   (o trecho entre `/d/` e `/edit` na URL).

**Uso:**

```bash
# confira antes, sem gravar nada
python main.py --source sheets --limit 5 --dry-run

# gravar as multas novas na planilha online
python main.py --source sheets --limit 21

# várias planilhas em sequência (repita --spreadsheet-id)
python main.py --source sheets --limit 21 \
               --spreadsheet-id "ID_DA_PLANILHA_A" --spreadsheet-id "ID_DA_PLANILHA_B"
```

> ⚠ O modo `xlsx` gera o arquivo de saída com openpyxl, que **não preserva** comentários
> encadeados e desenhos embutidos. Use-o para conferência; a gravação definitiva é no
> Google Sheets.

## Regras de negócio

Depois de consultar cada carro, o programa registra as multas **pendentes** que ainda não
estão na aba `CONTROLE DE MULTAS`:

- **Pendente** = situação "A Vencer", "Vencida" ou "Não Paga". Multas **pagas**, anuladas
  ou sem situação são ignoradas.
- **Deduplicação** pelo **Número do AI** (coluna D = `serieAIT`): se o AI já existe na aba,
  a multa é pulada — inclusive dentro da mesma execução.
- Cada multa nova vira uma linha com as colunas **B–F**:

  | Coluna | Origem |
  |---|---|
  | B CARRO | nome do carro (aba `LISTA DE CARROS`) |
  | C DATA DA INFRAÇÃO | data da infração |
  | D NÚMERO DO AI | `serieAIT` |
  | E ORGÃO AUTUADOR | código → texto da aba `ORGÃO AUTUADOR` (ex.: `000100 - PRF`) |
  | F VALOR | valor numérico (ex.: `130.16`) |

## Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| `Google Chrome não encontrado` | Instale o Chrome, ou adicione o caminho em `CHROME_CANDIDATES` (`browser.py`). |
| Pede login toda hora | A janela do Chrome foi fechada — o token vive no `sessionStorage`. Rode `--login` de novo e deixe a janela aberta. |
| Todos os veículos retornam erro | Sessão expirada, ou a página de consulta mudou. Refaça o `--login` e teste a consulta manualmente no navegador. |
| Porta `9222` ocupada | Outro Chrome com debug aberto: feche-o ou use `--cdp-port 9333`. |
| Nada é gravado | Está com `--dry-run`, ou todas as multas já constam na aba (dedup por AI). |
| Erro de permissão no Sheets | A planilha não foi compartilhada como **Editor** com o e-mail da Service Account. |

## Estrutura do projeto

```
app.py                entrada do programa empacotado: tela, --run (agendado) e --login
gui.py                tela Tkinter: configuração, execução com log ao vivo, histórico
main.py               a CLI (traduz os argumentos para um Config e chama o runner)
runner.py             a pipeline: carrega dados → Chrome/sessão → consulta → filtra → grava
config.py             Config persistido em JSON na pasta de dados do usuário
scheduler.py          agendamento diário no schtasks (Windows) / launchd (macOS)
history.py            histórico das execuções em history.jsonl
browser.py            lança o Chrome genuíno com porta de debug e anexa via CDP
detran_client.py      consulta 1 veículo na sessão logada e devolve o JSON de infrações
vehicles_source.py    leitura da aba LISTA DE CARROS (xlsx)
controle_multas.py    regras da aba CONTROLE DE MULTAS: pendência, dedup, mapeamento, XlsxSink
sheets.py             backend Google Sheets ao vivo (leitura + SheetsSink) via Service Account
detranExtractor.spec  receita do PyInstaller
```

`runner.py` é neutro quanto à interface: recebe um callback `log()` e um `ask_login()`.
A CLI passa `print`/`input`, a tela passa uma fila e uma caixa de diálogo, e o modo
agendado passa `ask_login=None` — que é justamente o que impede uma execução automática
de ficar travada esperando alguém digitar algo.

As funções `*_from_rows()` em `vehicles_source.py` e `controle_multas.py` trabalham sobre
sequências genéricas de linhas — o `sheets.py` reaproveita as mesmas funções. **Nova lógica
de parsing vai nessa camada**, para que os dois backends a herdem.

## Contribuindo

Contribuições são bem-vindas. O projeto ainda não tem testes automatizados nem linter
configurado, então:

1. Abra uma issue descrevendo o bug ou a ideia antes de um PR grande.
2. Mantenha os textos de usuário e a documentação **em português**.
3. Faça um smoke test dos imports após editar:

   ```bash
   python -c "import app, gui, runner, config, scheduler, history, main, sheets, controle_multas, vehicles_source, detran_client, browser; print('imports OK')"
   ```

4. Não commite dados reais: planilhas com placas/renavam, `credentials.json` ou perfis do
   Chrome (`.pw-profile/`). Todos já estão no `.gitignore`.

Ideias no radar: marcar `CONSULTADO` / `ÚLTIMA CONFERÊNCIA` na planilha, suporte a outros
DETRANs estaduais e agendamento via cron (mantendo o Chrome logado em background).

## Aviso legal

Ferramenta de uso pessoal para consultar **a própria frota**. O site do DETRAN-RS restringe
extração massiva automatizada:

- mantenha o throttle (`--min-delay` / `--max-delay`) e lotes pequenos (`--limit`);
- não aumente a concorrência nem remova as esperas;
- o login é feito **por você**, manualmente — o projeto não contorna captcha nem
  autenticação.

O uso é de responsabilidade de quem executa. Os dados consultados podem conter informações
pessoais: trate-os conforme a LGPD.

## Licença

MIT — veja [LICENSE](LICENSE).
