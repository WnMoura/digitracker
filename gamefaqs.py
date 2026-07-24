"""Baixa guias/walkthroughs do GameFAQs.

Substitui o passo manual que existia antes (rodar um scraper à parte, jogar o
texto numa IA, montar um PDF e importar): aqui você cola a URL e o app resolve.

A técnica de acesso é a mesma do scraper que já funcionava: `cloudscraper` com
perfil de Chrome/Windows e **sem headers customizados** — mexer nos headers
quebra a detecção do desafio do Cloudflare. Somam-se retentativas com espera
aleatória e um intervalo entre páginas, para não parecer um robô apressado.

Uso responsável: isto é para consumo pessoal, em volume baixo. O texto dos FAQs
é de autoria de quem escreveu — fica no seu `config/games/*.json` local, não é
para redistribuir.

As funções de parsing são puras (recebem HTML, devolvem dados) para poderem ser
testadas sem rede.
"""
from __future__ import annotations

import random
import re
import time
from urllib.parse import urljoin, urlparse

HOST = "gamefaqs.gamespot.com"

# Uma página de FAQ formatado é paginada; sem teto, um guia gigante viraria
# dezenas de requisições.
MAX_PAGES = 15
MIN_TEXT_CHARS = 500          # abaixo disso a página não é o guia (erro/captcha)
RETRIES = 3
PAGE_DELAY = (3.0, 7.0)       # respiro entre páginas, como no scraper original


class GameFAQsError(Exception):
    """Falha ao acessar ou interpretar o GameFAQs."""


def create_session():
    """Sessão que resolve o desafio do Cloudflare.

    NÃO adicione headers customizados: o cloudscraper monta um conjunto
    coerente com o navegador que ele finge ser, e sobrescrever qualquer campo
    faz o desafio falhar.
    """
    try:
        import cloudscraper
    except ImportError as exc:      # pragma: no cover - depende do ambiente
        raise GameFAQsError(
            "A biblioteca cloudscraper não está instalada (pip install cloudscraper)."
        ) from exc
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


# ---------------------------------------------------------------------------- #
# URLs
# ---------------------------------------------------------------------------- #
_FAQ_LINK_RE = re.compile(r"/faqs/(\d+)")


def normalize_url(url: str) -> str:
    """Aceita a URL colada pelo usuário e valida que é do GameFAQs."""
    url = (url or "").strip()
    if not url:
        raise GameFAQsError("Cole a URL do jogo ou do guia no GameFAQs.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("gamefaqs.gamespot.com"):
        raise GameFAQsError("Essa URL não é do GameFAQs (gamefaqs.gamespot.com).")
    return url


def is_faq_url(url: str) -> bool:
    """`/faqs/12345` é um guia específico; `/faqs` é a lista de guias."""
    return bool(_FAQ_LINK_RE.search(urlparse(url).path or ""))


# ---------------------------------------------------------------------------- #
# Parsing (puro — testável sem rede)
# ---------------------------------------------------------------------------- #
def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html or "", "html.parser")


def parse_faq_listing(html: str, base_url: str) -> list[dict]:
    """Guias disponíveis na página `/faqs` de um jogo: [{title, url}].

    A página repete o mesmo link em vários lugares (capa, tabela, destaques),
    então deduplicamos pelo id do FAQ, ficando com o título mais descritivo.
    """
    found: dict[str, dict] = {}
    for a in _soup(html).find_all("a", href=True):
        m = _FAQ_LINK_RE.search(a["href"])
        if not m:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if not title or title.lower() in ("faq", "guide", "walkthrough"):
            continue                      # rótulo genérico de navegação
        faq_id = m.group(1)
        prev = found.get(faq_id)
        if prev is None or len(title) > len(prev["title"]):
            found[faq_id] = {
                "id": faq_id,
                "title": title,
                "url": urljoin(base_url, a["href"].split("?")[0]),
            }
    return sorted(found.values(), key=lambda f: f["title"].lower())


def parse_faq_content(html: str) -> str:
    """Texto do guia. Mesma cascata do scraper original: a div do FAQ, senão um
    `<pre>`, senão a página inteira."""
    soup = _soup(html)
    node = soup.find("div", class_="faqtext") or soup.find("pre") or soup.body
    if node is None:
        return ""
    return node.get_text("\n")


def parse_page_count(html: str) -> int:
    """Quantas páginas tem o guia formatado (1 se não for paginado)."""
    pages = {1}
    for a in _soup(html).find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m:
            pages.add(int(m.group(1)) + 1)   # o parâmetro é 0-indexado
    return min(max(pages), MAX_PAGES)


def parse_faq_title(html: str) -> str:
    soup = _soup(html)
    for sel in ("h1.page-title", "h1", "title"):
        node = soup.select_one(sel)
        if node:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                return text
    return ""


# ---------------------------------------------------------------------------- #
# Rede
# ---------------------------------------------------------------------------- #
def _get(session, url: str, retries: int = RETRIES) -> str:
    """GET com retentativa e espera crescente/aleatória."""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:                 # rede, TLS, challenge…
            last = str(exc)
        else:
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                raise GameFAQsError("Página não encontrada no GameFAQs (404).")
            if resp.status_code == 403:
                last = "403 — o Cloudflare bloqueou o acesso."
            else:
                last = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(random.uniform(3, 6) * attempt)
    raise GameFAQsError(f"Não consegui baixar do GameFAQs: {last}")


def list_faqs(session, url: str) -> list[dict]:
    """Guias de um jogo. Se a URL já for de um guia, devolve só ele."""
    url = normalize_url(url)
    if is_faq_url(url):
        html = _get(session, url)
        return [{"id": _FAQ_LINK_RE.search(url).group(1),
                 "title": parse_faq_title(html) or "Guia", "url": url}]
    html = _get(session, url)
    faqs = parse_faq_listing(html, url)
    if not faqs:
        raise GameFAQsError(
            "Nenhum guia encontrado nessa página. Confira se a URL é a aba "
            "'FAQs/Guides' do jogo."
        )
    return faqs


def fetch_faq(session, url: str, on_progress=None) -> dict:
    """Baixa o guia inteiro (seguindo a paginação) -> {title, text, pages}."""
    url = normalize_url(url)
    first = _get(session, url)
    title = parse_faq_title(first)
    parts = [parse_faq_content(first)]
    total = parse_page_count(first)

    for page in range(1, total):
        if on_progress:
            on_progress(page + 1, total)
        time.sleep(random.uniform(*PAGE_DELAY))
        parts.append(parse_faq_content(_get(session, f"{url}?page={page}")))

    text = "\n".join(p for p in parts if p).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise GameFAQsError(
            "A página veio sem o texto do guia — o GameFAQs pode ter pedido "
            "verificação. Tente de novo em alguns minutos."
        )
    return {"title": title, "text": text, "pages": total}
