"""Parser de guias/walkthrough em PDF (texto já extraído) para o DigiTracker.

Os guias seguem uma estrutura previsível (ver Digimon_World_4_Walkthrough_PT):

  - Cabeçalho/rodapé repetido em toda página (filtrado).
  - Seções de nível 1:  `N. TÍTULO EM MAIÚSCULAS`  (N de 1 a 9).
  - Subseções:          `N.N Título`  (ex.: 4.1 Death Valley, 8.2 Modo Hard).
  - Walkthrough (seção 4): passos `#N Título` com linhas
    `Área:`, `Objetivo:`, `Dica:` e chefes `■ BOSS: NOME` + estratégia.
  - Conquistas (seção 8): `★ Nome` / `  P pts` / descrição / `  ■ ~` (separador),
    agrupadas por subseção que indica o **modo** (Normal/Hard/Super Hard).

Duas saídas:
  - `parse_guide(text)`  -> seções (para a aba de Dicas & Tutoriais) + conquistas.
  - `order_from_guide(parsed, meta, full_text)` -> ids das conquistas na ordem do
    guia + modo sugerido (normal/hard), com fallback por posição no texto.

Funções puras (sem I/O) — fáceis de testar.
"""
from __future__ import annotations

import difflib
import re
import unicodedata


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


# Cabeçalho/rodapé que se repete em toda página (ruído a remover).
_FURNITURE = [
    re.compile(r"^DIGIMON WORLD 4\b.*GUIA COMPLETO\s*$", re.I),
    re.compile(r"^P[áa]gina\s+\d+\s*$", re.I),
    re.compile(r"^RetroAchievements\b.*GameCube", re.I),
]

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


def _mode_from(name: str, category: str) -> str:
    """Modo (normal/hard) a partir do nome da conquista e da subseção.
    Super Hard / Very Hard são mapeados para 'hard' (o app só tem N/H)."""
    n = normalize(name)
    if re.search(r"\((?:vh|hard|h|super hard|very hard)\)", n):
        return "hard"
    if re.search(r"\((?:normal|n)\)", n):
        return "normal"
    c = normalize(category)
    if "hard" in c:          # 'modo hard', 'super hard / very hard'
        return "hard"
    return "normal"


def mode_from_difficulty(text: str) -> str:
    """Infere o modo (normal/hard) a partir de qualquer texto (título/descrição
    do RA). Hard / Very Hard / Super Hard -> 'hard'; o resto -> 'normal'."""
    t = normalize(text)
    if re.search(r"\b(?:hard|very hard|super hard)\b", t):
        return "hard"
    return "normal"


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
    return {
        "name": ach["name"],
        "pts": ach["pts"] or 0,
        "desc": " ".join(ach["desc"]).strip(),
        "mode": _mode_from(ach["name"], category),
        "category": category,
    }


# ---------------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------------- #
def parse_guide(text: str) -> dict:
    """Quebra o texto do guia em seções (com blocos classificados) e extrai a
    lista de conquistas da seção 8 (em ordem, com modo sugerido)."""
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(p.match(s) for p in _FURNITURE):
            continue
        lines.append(ln)

    sections: list[dict] = []
    achievements: list[dict] = []
    cur: dict | None = None
    in8 = False
    category = ""
    cur_ach: dict | None = None

    for raw in lines:
        s = raw.strip()

        hdr = _is_section_header(s)
        if hdr:
            if cur_ach:
                achievements.append(_finalize_ach(cur_ach, category))
                cur_ach = None
            cur = {"num": hdr[0], "title": hdr[1], "blocks": []}
            sections.append(cur)
            in8 = hdr[0] == "8"
            category = ""
            continue

        if cur is None:           # capa / índice antes da seção 1
            continue

        if in8:
            sub = _SUBSEC_RE.match(s)
            if sub and sub.group(1) == "8":
                if cur_ach:
                    achievements.append(_finalize_ach(cur_ach, category))
                    cur_ach = None
                category = sub.group(3).strip()
                cur["blocks"].append({"type": "subhead", "text": s})
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
            cur["blocks"].append(blk)

    if cur_ach:
        achievements.append(_finalize_ach(cur_ach, category))

    return {"sections": sections, "achievements": achievements}


# ---------------------------------------------------------------------------- #
# Ordenação das conquistas pela ordem do guia
# ---------------------------------------------------------------------------- #
_ACCEPT = 0.84   # score mínimo para aceitar um casamento


def order_from_guide(parsed: dict, achievements_meta: dict, full_text: str = "") -> dict:
    """Casa as conquistas importadas do RA (`{id: {title, points}}`) com a lista
    extraída da seção de conquistas do guia e devolve os ids na ordem do guia +
    modo sugerido (N/H). O casamento é robusto: nome exato → sem sufixo de
    dificuldade → similaridade (fuzzy) → com os pontos como desempate (decisivo
    para famílias como Undead Yard Normal/Hard/VH). Conquistas que não casarem
    pelo nome caem para a ordem por posição no texto; o resto vai em `missing`."""
    items = []
    for sid, meta in achievements_meta.items():
        t = normalize(meta.get("title", ""))
        items.append({
            "id": int(sid),
            "title": meta.get("title", ""),
            "t": t,
            "tsd": _strip_diff(t),
            "pts": int(meta.get("points") or 0),
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
    modes: dict[int, str] = {}
    report: list[dict] = []
    unmatched_pdf: list[str] = []

    for ach in parsed.get("achievements", []):
        it, score, how = best_match(ach["name"], int(ach.get("pts") or 0))
        if it and score >= _ACCEPT:
            it["used"] = True
            ordered_ids.append(it["id"])
            modes[it["id"]] = ach["mode"]
            report.append({"pdf": ach["name"], "ra": it["title"],
                           "score": round(score, 2), "how": how, "mode": ach["mode"]})
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
        (by_pos if pos >= 0 else missing).append((pos, it["id"]))
    for _, aid in sorted(by_pos):
        ordered_ids.append(aid)
        modes.setdefault(aid, "normal")

    return {
        "ordered_ids": ordered_ids,
        "missing_ids": [aid for _, aid in missing],
        "modes": modes,
        "found": len(ordered_ids),
        "matched_by_name": len(report),
        "total": len(items),
        "pdf_total": len(parsed.get("achievements", [])),
        "unmatched_pdf": unmatched_pdf,
        "report": report,
    }
