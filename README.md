# detranExtractor

Consulta as multas (infrações) de uma frota de veículos no DETRAN-RS.

Lê a lista de veículos de uma planilha `.xlsx` local, consulta a **consulta pública**
(por placa + renavam) do site do DETRAN-RS usando um navegador real (Playwright) e
imprime o resultado no console.

## Como funciona

O site `pcsdetran.rs.gov.br` é um SPA Angular e **exige login no gov.br** (a Central de
Serviços do DetranRS). A consulta de infrações é feita por placa + renavam, mas só depois
de autenticado.

Dois obstáculos e como o programa os resolve:

- **Captcha do gov.br**: o reCAPTCHA da tela de login rejeita navegadores com marca de
  automação (`navigator.webdriver = true`, que o Playwright liga ao lançar o navegador).
  Solução: o programa **não lança o navegador com automação** — ele abre um **Google
  Chrome genuíno** (com porta de debug) e só se **anexa** a ele via CDP. O login acontece
  como num navegador normal.
- **Token no `sessionStorage`**: o app guarda o token de acesso no `sessionStorage`, que
  o Chrome apaga ao fechar. Por isso não adianta logar e reabrir depois. Solução: o
  mesmo Chrome fica **vivo** entre execuções; enquanto a janela estiver aberta, a sessão
  segue válida e não é preciso logar de novo.

Com a sessão ativa, o programa navega para a consulta de cada veículo e intercepta o XHR
que o próprio app dispara:

```
GET https://pcsdetran.procergs.com.br/pcsdetran/rest/infracoes/veiculos/publicas/{PLACA}?renavam={RENAVAM}
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # baixa os drivers do Playwright (usamos o Chrome do sistema)
```

## Uso

**1) Logar no gov.br** (abre o Chrome; faça o login e volte ao terminal):

```bash
python main.py --login
```

Isso abre uma janela do Chrome. Clique em **"Entrar com o gov.br"**, faça o login
(CPF/senha + 2º fator) e, quando a página de consulta aparecer, pressione **ENTER** no
terminal. **Não feche essa janela do Chrome** — é ela que mantém a sessão viva.

**2) Consultar os veículos** (com o Chrome ainda aberto):

```bash
python main.py --limit 5
python main.py --xlsx "CONTROLE DE MULTAS VICTOR.xlsx" --limit 10 --min-delay 6 --max-delay 15
```

Enquanto a janela do Chrome continuar aberta, os próximos comandos nem pedem login.
Se você fechar o Chrome, rode `python main.py --login` de novo.

Argumentos:

| flag | default | descrição |
|------|---------|-----------|
| `--login` | — | abre o Chrome e espera você logar no gov.br (não consulta) |
| `--xlsx` | `CONTROLE DE MULTAS VICTOR.xlsx` | caminho da planilha |
| `--limit` | `5` | máximo de veículos a consultar |
| `--out-xlsx` | `CONTROLE DE MULTAS VICTOR - TESTE.xlsx` | arquivo de saída onde as multas novas são gravadas |
| `--dry-run` | — | só mostra o que seria adicionado, sem gravar |
| `--min-delay` / `--max-delay` | `6` / `15` | throttle (segundos) entre veículos |
| `--cdp-port` | `9222` | porta de debug do Chrome |

A planilha deve ter a aba **`LISTA DE CARROS`** com colunas `A=CARRO`, `B=PLACA`,
`C=RENAVAM`.

## Gravação de multas novas (aba CONTROLE DE MULTAS)

Depois de consultar cada carro, o programa registra as multas **pendentes** que ainda não
estão na aba `CONTROLE DE MULTAS`:

- **Pendente** = situação "A Vencer", "Vencida" ou "Não Paga" (multas **Pagas**, anuladas
  ou sem valor são ignoradas).
- **Deduplicação** pelo **Número do AI** (coluna D = `serieAIT`): se o AI já existe na
  aba, a multa é pulada.
- Para cada multa nova, é adicionada uma linha com as colunas **B–F**:

  | Coluna | Origem |
  |---|---|
  | B CARRO | nome do carro (aba LISTA DE CARROS) |
  | C DATA DA INFRAÇÃO | data da infração |
  | D NÚMERO DO AI | `serieAIT` |
  | E ORGÃO AUTUADOR | código do órgão → texto da aba `ORGÃO AUTUADOR` (ex.: `000100 - PRF`) |
  | F VALOR | valor numérico (ex.: `130.16`) |

A gravação pode ir para um **arquivo .xlsx de teste** (`--source xlsx`, default) ou
**direto no Google Sheets ao vivo** (`--source sheets`). Use `--dry-run` para só ver o
que seria adicionado, sem gravar.

> ⚠ O modo xlsx gera o arquivo de saída com openpyxl, que **não preserva** comentários
> encadeados e desenhos embutidos. Por isso é só para conferência — a gravação definitiva
> é no Google Sheets.

## Google Sheets ao vivo (`--source sheets`)

Lê os veículos e grava as multas novas **direto na planilha online**, via Service Account
(sem login interativo — ideal para cron).

**Setup (uma vez):**

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/) → crie um projeto.
2. Em **APIs e serviços → Biblioteca**, habilite a **Google Sheets API**.
3. Em **APIs e serviços → Credenciais → Criar credenciais → Conta de serviço**; crie a
   conta e, nela, **Chaves → Adicionar chave → JSON**. Baixe o arquivo e salve como
   `credentials.json` na pasta do projeto.
4. Copie o e-mail da conta de serviço (algo como
   `nome@projeto.iam.gserviceaccount.com`) e **compartilhe a planilha como _Editor_**
   com esse e-mail (botão Compartilhar no Google Sheets).

**Uso:**

```bash
# confira antes, sem gravar nada:
python main.py --source sheets --limit 5 --dry-run

# gravar as multas novas na planilha online:
python main.py --source sheets --limit 21
```

Flags relacionadas: `--credentials` (caminho do JSON, default `credentials.json`).
O ID da planilha está fixo em `sheets.py` (`DEFAULT_SPREADSHEET_ID`).

## Avisos importantes

- **Login gov.br obrigatório**: a consulta só funciona autenticado, e o token vive
  enquanto o Chrome estiver aberto. Fechou o Chrome → rode `--login` de novo.
- **Termos de uso**: o site restringe extração massiva automatizada. O uso legítimo
  (conferir a própria frota) deve ser feito com moderação — mantenha o throttling e
  lotes pequenos para não sofrer bloqueio/rate-limit.

## Próximos passos (planejado)

- **Google Sheets ao vivo** (destino final): ler os veículos e **gravar** as multas novas
  direto na planilha online via **Service Account** do Google Cloud (habilitar a Google
  Sheets API, criar a conta de serviço, compartilhar a planilha **como editor** com o
  e-mail dela). Novo sink `SheetsSink` ao lado do `XlsxSink`; o resto da lógica não muda.
- Persistir o resultado (marcar `CONSULTADO` / `ÚLTIMA CONFERÊNCIA`).
- Agendar em cron (mantendo o Chrome logado vivo em background).

## Arquivos

- `main.py` — orquestra: abre/anexa o Chrome, garante login, consulta e grava as novas.
- `browser.py` — lança o Chrome genuíno com porta de debug e anexa via CDP (`connect_over_cdp`).
- `vehicles_source.py` — fonte de dados (hoje: xlsx). Interface `read_vehicles()`.
- `detran_client.py` — consulta 1 veículo na sessão logada e devolve o JSON de infrações.
- `controle_multas.py` — regras da aba CONTROLE DE MULTAS: pendência, dedup por AI,
  mapeamento B–F e o `XlsxSink` de gravação.
- `sheets.py` — backend Google Sheets ao vivo (leitura + `SheetsSink`) via Service Account.
