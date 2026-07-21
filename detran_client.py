"""Consulta as infrações públicas de um veículo no DETRAN-RS via navegador real.

O site é um SPA Angular protegido por captcha invisível (reCAPTCHA v3 + Cloudflare
Turnstile), cujos tokens são gerados por JavaScript e validados no backend. Por isso
usamos um navegador real (Playwright): abrimos a página de consulta pública e
interceptamos o XHR que o próprio app dispara:

    GET https://pcsdetran.procergs.com.br/pcsdetran/rest/infracoes/veiculos/publicas/{PLACA}?renavam={RENAVAM}
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError

from vehicles_source import Vehicle

PAGE_URL = "https://pcsdetran.rs.gov.br/consulta-infracoes-veiculo/publicas/{placa}?renavam={renavam}"
CONSULTA_URL = "https://pcsdetran.rs.gov.br/consulta-infracoes-veiculo"
API_MARKER = "/infracoes/veiculos/publicas/"
LOGIN_WALL_TEXT = "exige que você se conecte"


def is_login_wall(page) -> bool:
    """Detecta a tela que pede login gov.br (sessão ausente/expirada)."""
    try:
        return page.get_by_text(LOGIN_WALL_TEXT).count() > 0
    except Exception:  # noqa: BLE001
        return False


@dataclass
class ConsultaResult:
    vehicle: Vehicle
    ok: bool
    status: int | None = None
    infracoes: list | None = None
    error: str | None = None


def consultar_veiculo(context: BrowserContext, vehicle: Vehicle, timeout_ms: int = 45000) -> ConsultaResult:
    """Abre a consulta pública do veículo e devolve o JSON de infrações interceptado."""
    page = context.new_page()

    try:
        url = PAGE_URL.format(placa=vehicle.placa, renavam=vehicle.renavam)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1500)

        if is_login_wall(page):
            return ConsultaResult(
                vehicle=vehicle,
                ok=False,
                error="Sessão não autenticada. Rode `python main.py --login` (e não feche o Chrome).",
            )

        # Fixa o filtro em "Todas" para a consulta ser determinística (o padrão da
        # página varia entre execuções).
        try:
            page.get_by_text("Todas", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(300)
        except PlaywrightTimeoutError:
            pass

        # O link pré-preenche placa/renavam, mas a busca só dispara ao clicar em
        # "Consultar". O clique gera os tokens de captcha e chama a API — que nós
        # interceptamos.
        botao = page.get_by_role("button", name="Consultar").first
        try:
            botao.wait_for(state="visible", timeout=timeout_ms)
            with page.expect_response(
                lambda r: API_MARKER in r.url, timeout=timeout_ms
            ) as resp_info:
                botao.click()
            response = resp_info.value
        except PlaywrightTimeoutError:
            return ConsultaResult(
                vehicle=vehicle,
                ok=False,
                error="Nenhuma resposta da API de infrações após clicar em Consultar (captcha/timeout?).",
            )

        status = response.status
        json_error = None
        body = None
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - guardamos o erro para diagnóstico
            json_error = str(exc)

        return ConsultaResult(
            vehicle=vehicle,
            ok=status < 400 and json_error is None,
            status=status,
            infracoes=_extrair_lista(body),
            error=json_error,
        )
    except Exception as exc:  # noqa: BLE001
        return ConsultaResult(vehicle=vehicle, ok=False, error=str(exc))
    finally:
        page.close()


def _extrair_lista(body) -> list:
    """Normaliza o corpo da resposta em uma lista de infrações.

    O formato exato do JSON é confirmado na 1ª execução; aqui cobrimos os casos comuns
    (lista direta, ou objeto com uma chave contendo a lista).
    """
    if body is None:
        return []
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("infracoes", "content", "data", "lista", "results"):
            val = body.get(key)
            if isinstance(val, list):
                return val
        # Se for um dict sem lista reconhecível, devolve-o embrulhado para inspeção.
        return [body]
    return []
