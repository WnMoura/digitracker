"""Parser de guias/walkthrough em PDF (texto já extraído) para o DigiTracker.

Os guias seguem uma estrutura previsível:

  - Cabeçalho/rodapé repetido em toda página — **detectado por repetição**, não
    por uma lista fixa de textos (ver `detect_furniture`).
  - Seções de nível 1:  `N. TÍTULO EM MAIÚSCULAS`  (N de 1 a 9).
  - Subseções:          `N.N Título`  (ex.: 4.1 Death Valley, 8.2 Modo Hard).
  - Walkthrough: passos `#N Título` com linhas `Área:`, `Objetivo:`, `Dica:` e
    chefes `■ BOSS: NOME` + estratégia.
  - Conquistas: `★ Nome` / `  P pts` / descrição / `  ■ ~` (separador), agrupadas
    por subseção que indica o **modo** (Normal/Hard/Super Hard). Qual seção é a
    de conquistas é **descoberto pelo conteúdo** (ver `find_ach_section`) — não
    se assume que seja a de número 8.

Duas saídas:
  - `parse_guide(text)`  -> seções (para a aba de Dicas & Tutoriais) + conquistas.
    A seção de conquistas vem marcada com `is_achievements: True`.
  - `order_from_guide(parsed, meta, full_text)` -> ids das conquistas na ordem do
    guia + modo sugerido (normal/hard), com fallback por posição no texto.

Funções puras (sem I/O) — fáceis de testar.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter


# ---------------------------------------------------------------------------- #
# Normalização / utilidades
# ---------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Sem acentos, minúsculo, espaços colapsados."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


# Sufixos de dificuldade que o guia adiciona mas o set do RA pode não ter
# (ou ter de outra forma): (normal), (hard), (vh), (n), [hard], etc.
_DIFF_SUFFIX = re.compile(
    r"\s*[\(\[\{]\s*(?:normal|hard|very hard|super hard|vh|sh|n|h)\s*[\)\]\}]\s*$",
    re.I,
)


def _strip_diff(norm: str) -> str:
    """Remove o sufixo de dificuldade do fim do nome, repetidamente."""
    prev = None
    cur = norm
    while cur != prev:
        prev = cur
        cur = _DIFF_SUFFIX.sub("", cur).strip()
        cur = re.sub(r"\s*\([^)]*\d[^)]*\)\s*$", "", cur).strip()  # ex.: (50) pontos
    return cur


def _tokens(norm: str) -> set:
    return set(re.findall(r"[a-z0-9]+", norm))


def _similarity(a: str, b: str) -> float:
    """Mistura ratio de sequência com Jaccard de tokens (0..1)."""
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    return max(ratio, 0.5 * ratio + 0.5 * jac)


# Numeração de página — sempre ruído, em qualquer guia.
_PAGE_NUM_RE = re.compile(r"^(?:p[áa]g(?:ina)?\.?|page)?\s*\d{1,4}\s*(?:/\s*\d{1,4})?\s*$", re.I)

# Seção de nível 1: "N. TÍTULO".  Subseção: "N.N Título".
_SECTION_RE = re.compile(r"^([1-9])\.\s+(.+)$")
_SUBSEC_RE = re.compile(r"^(\d)\.(\d+)\s+(.+)$")
# Conquistas (seção 8)
_ACH_RE = re.compile(r"^★\s*(.+)$")
_PTS_RE = re.compile(r"^(\d+)\s*pts$", re.I)
_SEP_RE = re.compile(r"^■\s*(?:~|\d+)$")
# Walkthrough
_STEP_RE = re.compile(r"^#(\d+)\s+(.+)$")
_BOSS_RE = re.compile(r"^■\s*BOSS:\s*(.+)$", re.I)
_LABEL_RE = re.compile(r"^([A-Za-zÀ-ÿ][\wÀ-ÿ /.\-]{1,22}):\s*(.*)$")

_LABELS = {
    "area", "objetivo", "dica", "local", "requisito", "recompensa",
    "caracteristica", "forca", "fraqueza", "especialista", "efeito", "uso",
    "pre-requisito", "nota", "nivel recomendado", "modo", "evolucao",
}


def _is_section_header(stripped: str):
    """Retorna (num, titulo) se a linha é cabeçalho de seção de nível 1."""
    m = _SECTION_RE.match(stripped)
    if not m:
        return None
    title = m.group(2).strip().strip("■").strip()
    letters = [c for c in title if c.isalpha()]
    if len(letters) < 4:
        return None
    upper = sum(1 for c in letters if c.isupper())
    if upper / len(letters) < 0.6:   # cabeçalhos são MAIÚSCULOS
        return None
    return m.group(1), title


def _classify(raw: str):
    """Classifica uma linha de conteúdo num bloco para exibição na aba de dicas."""
    s = raw.strip()
    if not s:
        return None
    m = _BOSS_RE.match(s)
    if m:
        return {"type": "boss", "text": m.group(1).strip()}
    m = _STEP_RE.match(s)
    if m:
        return {"type": "step", "n": int(m.group(1)), "text": m.group(2).strip()}
    m = _SUBSEC_RE.match(s)
    if m:
        return {"type": "subhead", "text": f"{m.group(1)}.{m.group(2)} {m.group(3).strip()}"}
    if s.startswith("■"):
        return {"type": "note", "text": s.lstrip("■").strip()}
    m = _LABEL_RE.match(s)
    if m and normalize(m.group(1)) in _LABELS:
        return {"type": "label", "label": m.group(1).strip(), "text": m.group(2).strip()}
    if raw[:1] in (" ", "\t"):          # bullets perdem o glifo e viram recuo
        return {"type": "li", "text": s}
    return {"type": "p", "text": s}


def _finalize_ach(ach: dict, category: str) -> dict:
    """`category` é a subseção onde a conquista aparece (ex.: "Modo Hard").
    Fica só como rótulo informativo: a dificuldade do jogo não é mais um eixo do
    app — o que separa as conquistas hoje é softcore/hardcore, que vem da API."""
    return {
        "name": ach["name"],
        "pts": ach["pts"] or 0,
        "desc": " ".join(ach["desc"]).strip(),
        "category": category,
    }


# ---------------------------------------------------------------------------- #
# Detecção de cabeçalho/rodapé (page furniture)
# ---------------------------------------------------------------------------- #
# Linhas estruturais nunca são ruído, por mais que se repitam.
_STRUCTURAL = (_ACH_RE, _PTS_RE, _SEP_RE, _STEP_RE, _SUBSEC_RE, _BOSS_RE)

_FURNITURE_MAX_LEN = 90     # cabeçalhos/rodapés são curtos
_FURNITURE_MIN_HITS = 3     # quantas páginas precisam repetir a linha
_SHORT_GUIDE_LINES = 80     # abaixo disso o guia tem poucas páginas


def _furniture_key(s: str) -> str:
    """Chave de repetição: normalizada e com os números mascarados, para que
    'Página 3' e 'Página 12' contem como a mesma linha."""
    return re.sub(r"\d+", "#", normalize(s))


def detect_furniture(lines, min_hits: int | None = None) -> set:
    """Descobre o cabeçalho/rodapé repetido em cada página, sem depender de uma
    lista fixa de textos: linha curta, não estrutural, sem pontuação de frase e
    que reaparece em várias páginas.

    O limiar de repetições se adapta ao tamanho do guia: num PDF de 2 páginas o
    cabeçalho só aparece 2 vezes, e exigir 3 o deixaria passar."""
    lines = list(lines)
    if min_hits is None:
        min_hits = 2 if len(lines) < _SHORT_GUIDE_LINES else _FURNITURE_MIN_HITS
    counts: Counter = Counter()
    for raw in lines:
        s = raw.strip()
        if not s or len(s) > _FURNITURE_MAX_LEN:
            continue
        if s.endswith((".", ":", ";", ",")):   # frases de corpo de texto
            continue
        if any(p.match(s) for p in _STRUCTURAL) or _is_section_header(s):
            continue
        counts[_furniture_key(s)] += 1
    return {k for k, n in counts.items() if k and n >= min_hits}


# ---------------------------------------------------------------------------- #
# Qual seção é a lista de conquistas
# ---------------------------------------------------------------------------- #
_ACH_TITLE_HINT = re.compile(r"conquista|achievement|trof[ée]u|troph", re.I)


def find_ach_section(sections) -> int:
    """Índice da seção que lista as conquistas, ou -1. Decide pelo conteúdo
    (quantas linhas `★ Nome` a seção tem) e, em empate ou ausência, pelo título.
    Não assume numeração — guias diferentes usam ordens diferentes."""
    best, best_hits = -1, 0
    for i, sec in enumerate(sections):
        hits = sum(1 for ln in sec["lines"] if _ACH_RE.match(ln.strip()))
        if hits > best_hits:
            best, best_hits = i, hits
    if best_hits >= 2:
        return best
    for i, sec in enumerate(sections):
        if _ACH_TITLE_HINT.search(sec["title"]):
            return i
    return best if best_hits else -1


# ---------------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------------- #
def _split_sections(lines) -> list[dict]:
    """Quebra as linhas em seções de nível 1, preservando as linhas cruas."""
    sections: list[dict] = []
    cur: dict | None = None
    for raw in lines:
        hdr = _is_section_header(raw.strip())
        if hdr:
            cur = {"num": hdr[0], "title": hdr[1], "lines": []}
            sections.append(cur)
            continue
        if cur is None:           # capa / índice antes da primeira seção
            continue
        cur["lines"].append(raw)
    return sections


def _parse_ach_section(sec: dict) -> tuple[list[dict], list[dict]]:
    """Faz o parsing da seção de conquistas. Devolve (blocos, conquistas)."""
    blocks: list[dict] = []
    achievements: list[dict] = []
    category = ""
    cur_ach: dict | None = None

    for raw in sec["lines"]:
        s = raw.strip()
        sub = _SUBSEC_RE.match(s)
        if sub and sub.group(1) == sec["num"]:
            if cur_ach:
                achievements.append(_finalize_ach(cur_ach, category))
                cur_ach = None
            category = sub.group(3).strip()
            blocks.append({"type": "subhead", "text": s})
            continue
        mach = _ACH_RE.match(s)
        if mach:
            if cur_ach:
                achievements.append(_finalize_ach(cur_ach, category))
            cur_ach = {"name": mach.group(1).strip(), "pts": None, "desc": []}
            continue
        if cur_ach is not None:
            mp = _PTS_RE.match(s)
            if mp:
                cur_ach["pts"] = int(mp.group(1))
                continue
            if _SEP_RE.match(s):
                achievements.append(_finalize_ach(cur_ach, category))
                cur_ach = None
                continue
            cur_ach["desc"].append(s)
            continue
        blk = _classify(raw)
        if blk:
            blocks.append(blk)

    if cur_ach:
        achievements.append(_finalize_ach(cur_ach, category))
    return blocks, achievements


# ---------------------------------------------------------------------------- #
# Guias de texto livre (FAQs do GameFAQs)
# ---------------------------------------------------------------------------- #
# Os FAQs não seguem o formato dos PDFs (`★`, `N. TÍTULO` em maiúsculas). São
# ASCII solto: títulos cercados por réguas (`=====`, `-----`) ou entradas
# numeradas isoladas por linhas em branco. O índice no topo tem as MESMAS
# entradas numeradas, mas em linhas consecutivas — é o isolamento que separa
# um cabeçalho de verdade de uma linha do índice.
_RULER_RE = re.compile(r"^\s*[-=*_~#+]{5,}\s*$")
_NUMBERED_HEAD_RE = re.compile(r"^\s*(?:\d{1,2}|[a-z]|[ivx]{1,4})\s*[.)]\s*(.{2,70})$", re.I)

_FREEFORM_MAX_BLOCKS = 2500   # um FAQ de 160KB viraria um JSON enorme no jogo
_FREEFORM_MAX_TITLE = 80
_NUMBERED_HEAD_MAX = 50       # acima disso, uma linha numerada é item de lista


def _clean_heading(text: str) -> str:
    """Tira as réguas que enfeitam o título:
    `---4.Side-Quests---  (sqst)---` -> `4.Side-Quests (sqst)`.

    Só sequências de 2+ símbolos viram espaço, para não estragar hífens de
    palavras compostas ("Side-Quests")."""
    cleaned = text.strip().strip("-=*_~#+ ").strip()
    cleaned = re.sub(r"[-=*_~#+]{2,}", " ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _looks_like_heading(line: str, prev: str, nxt: str) -> bool:
    """Cabeçalho de seção num FAQ de texto livre."""
    s = line.strip()
    if not s or len(s) > _FREEFORM_MAX_TITLE or _RULER_RE.match(s):
        return False
    if not any(c.isalpha() for c in s):
        return False

    # Régua ABAIXO do texto: cobre título sublinhado e o ensanduichado
    # (`====` / `---Título--` / `====`). Exigir a régua de baixo é o que impede
    # a primeira linha do corpo — logo após a régua de fechamento — de virar
    # cabeçalho.
    if _RULER_RE.match(nxt.strip()):
        return True

    # Precedido de linha em branco: é o que separa `4) Copyright info.` (título
    # real, com o texto logo abaixo) das MESMAS entradas no índice do topo, que
    # aparecem grudadas umas nas outras.
    starts_block = not prev.strip() or bool(_RULER_RE.match(prev.strip()))
    if not starts_block:
        return False
    if _NUMBERED_HEAD_RE.match(s):
        # Um título numerado é curto ("4) Copyright info.") ou fica sozinho no
        # parágrafo. Guias numeram também os ITENS de lista ("2. (Abyss of
        # Grief)-Mammothmon (300hp): Once again..."), que são frases longas
        # seguidas da continuação — esses não são seção.
        return len(s) <= _NUMBERED_HEAD_MAX or not nxt.strip()
    # Sem numeração, exigimos MAIÚSCULAS e isolamento dos dois lados: linha em
    # caixa alta no meio do texto é comum demais para bastar sozinha.
    if nxt.strip():
        return False
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.7


def parse_freeform(text: str, max_blocks: int = _FREEFORM_MAX_BLOCKS) -> dict:
    """Converte um FAQ de texto livre no MESMO formato de `parse_guide`, para a
    aba de Dicas renderizar sem saber de onde veio o guia.

    Linhas quebradas em ~78 colunas são recolhidas em parágrafos — sem isso cada
    linha viraria um bloco e o resultado ficaria ilegível (e gigante).
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body = [ln for ln in raw if not _RULER_RE.match(ln.strip())]
    furniture = detect_furniture(body)

    sections: list[dict] = []
    cur: dict | None = None
    buf: list[str] = []
    buf_indented = False
    total = 0
    truncated = False

    def flush():
        """Fecha o parágrafo acumulado."""
        nonlocal buf, total, truncated
        if not buf or cur is None:
            buf = []
            return
        if total >= max_blocks:
            truncated = True
            buf = []
            return
        cur["blocks"].append({
            "type": "li" if buf_indented else "p",
            "text": " ".join(buf).strip(),
        })
        total += 1
        buf = []

    for i, line in enumerate(raw):
        stripped = line.strip()
        prev = raw[i - 1] if i else ""
        nxt = raw[i + 1] if i + 1 < len(raw) else ""

        if _RULER_RE.match(stripped):
            flush()
            continue
        if not stripped:
            flush()
            continue
        # Título vem ANTES do filtro de ruído: um cabeçalho que também aparece
        # no índice conta 2x e seria descartado como rodapé repetido.
        if _looks_like_heading(line, prev, nxt):
            flush()
            title = _clean_heading(stripped)
            if title:
                cur = {"num": str(len(sections) + 1), "title": title,
                       "blocks": [], "is_achievements": False}
                sections.append(cur)
            continue

        if _furniture_key(line) in furniture or _PAGE_NUM_RE.match(stripped):
            continue

        if cur is None:
            continue          # índice/cabeçalho antes da primeira seção

        if not buf:
            buf_indented = line[:1] in (" ", "\t")
        buf.append(stripped)

    flush()

    if truncated and sections:
        sections[-1]["blocks"].append(
            {"type": "note", "text": "— guia truncado: o restante ficou de fora —"}
        )

    sections = [s for s in sections if s["blocks"]]
    for n, sec in enumerate(sections, start=1):
        sec["num"] = str(n)
    return {"sections": sections, "achievements": []}


def parse_guide(text: str) -> dict:
    """Quebra o texto do guia em seções (com blocos classificados) e extrai a
    lista de conquistas (em ordem, com modo sugerido) da seção que as contém."""
    raw_lines = [ln for ln in text.splitlines() if ln.strip()]
    furniture = detect_furniture(raw_lines)
    lines = [
        ln for ln in raw_lines
        if _furniture_key(ln) not in furniture and not _PAGE_NUM_RE.match(ln.strip())
    ]

    raw_sections = _split_sections(lines)
    ach_idx = find_ach_section(raw_sections)

    sections: list[dict] = []
    achievements: list[dict] = []
    for i, sec in enumerate(raw_sections):
        if i == ach_idx:
            blocks, achievements = _parse_ach_section(sec)
        else:
            blocks = [b for b in (_classify(ln) for ln in sec["lines"]) if b]
        sections.append({
            "num": sec["num"],
            "title": sec["title"],
            "blocks": blocks,
            "is_achievements": i == ach_idx,
        })

    return {"sections": sections, "achievements": achievements}


# ---------------------------------------------------------------------------- #
# Ordenação das conquistas pela ordem do guia
# ---------------------------------------------------------------------------- #
_ACCEPT = 0.84   # score mínimo para aceitar um casamento

# Nomes próprios compostos da descrição ("Humid Cave", "Cliff Dungeon").
_PROPER_PHRASE_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")
# Verbos que abrem a descrição e não ajudam a localizar nada no guia.
_DESC_VERBS = {
    "complete", "defeat", "clear", "obtain", "finish", "beat", "collect",
    "unlock", "earn", "reach", "win", "get", "find", "kill", "acquire",
    "conquiste", "derrote", "complete", "termine", "obtenha",
}


def _desc_anchors(desc: str) -> list[str]:
    """Lugares/chefes citados na descrição da conquista.

    Guias antigos do GameFAQs não trazem os nomes das conquistas — elas foram
    inventadas pela comunidade do RetroAchievements muito depois. A descrição,
    porém, cita o que o guia cita ("Complete Humid Cave" -> "humid cave"), e é
    isso que permite posicionar a conquista no texto.
    """
    out = []
    for phrase in _PROPER_PHRASE_RE.findall(desc or ""):
        parts = normalize(phrase).split()
        if len(parts) > 1 and parts[0] in _DESC_VERBS:
            parts = parts[1:]          # "complete humid cave" -> "humid cave"
        anchor = " ".join(parts)
        if len(anchor) >= 6:
            out.append(anchor)
    return out


def order_from_guide(parsed: dict, achievements_meta: dict, full_text: str = "") -> dict:
    """Casa as conquistas importadas do RA (`{id: {title, points}}`) com a lista
    extraída da seção de conquistas do guia e devolve os ids na ordem do guia.
    O casamento é robusto: nome exato → sem sufixo de dificuldade →
    similaridade (fuzzy) → com os pontos como desempate (decisivo para famílias
    como Undead Yard Normal/Hard/VH, que têm o mesmo nome). Conquistas que não
    casarem pelo nome caem para a ordem por posição no texto; o resto vai em
    `missing`."""
    items = []
    for sid, meta in achievements_meta.items():
        t = normalize(meta.get("title", ""))
        items.append({
            "id": int(sid),
            "title": meta.get("title", ""),
            "t": t,
            "tsd": _strip_diff(t),
            "pts": int(meta.get("points") or 0),
            "anchors": _desc_anchors(meta.get("desc", "")),
            "used": False,
        })

    def best_match(name: str, pts: int):
        """Devolve (item, score, método) do melhor candidato não usado."""
        n = normalize(name)
        nsd = _strip_diff(n)
        best, best_score, best_how = None, 0.0, ""
        for it in items:
            if it["used"] or not it["t"]:
                continue
            if it["t"] == n:
                name_score, how = 1.0, "exato"
            elif it["tsd"] and it["tsd"] == nsd:
                name_score, how = 0.95, "sem-sufixo"
            else:
                name_score, how = _similarity(it["t"], n), "fuzzy"
            score = name_score
            if pts and it["pts"]:                          # pontos como sinal extra
                score += 0.15 if it["pts"] == pts else -0.12
            if score > best_score:
                best, best_score, best_how = it, score, how
        return best, best_score, best_how

    ordered_ids: list[int] = []
    report: list[dict] = []
    unmatched_pdf: list[str] = []

    for ach in parsed.get("achievements", []):
        it, score, how = best_match(ach["name"], int(ach.get("pts") or 0))
        if it and score >= _ACCEPT:
            it["used"] = True
            ordered_ids.append(it["id"])
            report.append({"pdf": ach["name"], "ra": it["title"],
                           "score": round(score, 2), "how": how})
        else:
            unmatched_pdf.append(ach["name"])

    # fallback: conquistas do RA que não casaram pelo nome -> ordem por 1ª posição
    # no texto do guia (algumas aparecem citadas nas dicas).
    norm_text = normalize(full_text)
    by_pos, missing = [], []
    for it in items:
        if it["used"]:
            continue
        pos = norm_text.find(it["t"]) if (it["t"] and norm_text) else -1
        if pos < 0 and norm_text:
            # O nome da conquista não está no guia (comum em FAQs antigos):
            # localiza pelo lugar citado na descrição.
            for anchor in it["anchors"]:
                found = norm_text.find(anchor)
                if found >= 0 and (pos < 0 or found < pos):
                    pos = found
        (by_pos if pos >= 0 else missing).append((pos, it["id"]))
    for _, aid in sorted(by_pos):
        ordered_ids.append(aid)

    return {
        "ordered_ids": ordered_ids,
        "missing_ids": [aid for _, aid in missing],
        "found": len(ordered_ids),
        "matched_by_name": len(report),
        "total": len(items),
        "pdf_total": len(parsed.get("achievements", [])),
        "unmatched_pdf": unmatched_pdf,
        "report": report,
    }
