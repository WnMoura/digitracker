"""Fila retomável de imagens; 'complete' significa cobertura de todos os cards."""
from __future__ import annotations

import time

import atlas_images


ACTIVE_PHASES = {"running", "waiting_retry"}


def run(task: dict, *, nodes, current_media, search, approve, associate,
        cancelled, persist, wait, is_valid_media=lambda _media: True,
        known_labels: list[str] | None = None) -> None:
    """Callbacks isolate network/storage from the retry and coverage contract.

    A failed pass remains active with bounded backoff until filled/cancelled.
    Failed URLs do not prevent trying other candidates. Edits made after the
    job started always win through an atomic compare-and-set association.
    """
    options = task["options"]
    done = set(task.get("resolved_ids") or [])
    expected = task.setdefault("expected_media", {})
    task.setdefault("changes", [])
    task.setdefault("attempts", {})
    task.setdefault("issues", {})
    task.setdefault("pass", 0)
    while not cancelled():
        snapshot = nodes()
        by_id = {node["id"]: node for node in snapshot}
        done.intersection_update(by_id)
        pending = []
        for node in snapshot:
            node_id = node["id"]
            media = current_media(node_id)
            if node_id not in expected:
                expected[node_id] = media
            if not media:
                # A removed image (also after a restart) is missing again.
                done.discard(node_id)
                expected[node_id] = ""
            usable = bool(media and is_valid_media(media))
            if not usable:
                done.discard(node_id)
            if usable and (not options["replace_existing"] or node_id in done or media != expected[node_id]):
                done.add(node_id)
            if node_id not in done:
                pending.append(node)
        task.update(total=len(snapshot), completed=len(done), remaining=len(pending),
                    resolved_ids=sorted(done), pending_ids=[node["id"] for node in pending])
        if not pending:
            task["issues"] = {}
            task.update(phase="complete", next_retry_at=0, message="Todos os cartões estão com imagem.")
            persist(task)
            return
        task.update(phase="running", next_retry_at=0)
        task["pass"] += 1
        persist(task)
        for node in pending:
            if cancelled():
                break
            node_id, label = node["id"], node.get("label", "")
            attempt = int(task["attempts"].get(node_id, 0)) + 1
            task["attempts"][node_id] = attempt
            task.update(current_node_id=node_id, message=f"Buscando imagem do card #{node.get('card_number') or snapshot.index(node) + 1:03d} · tentativa {attempt}")
            persist(task)
            if not atlas_images.searchable_label(label, known_labels=known_labels):
                task["issues"][node_id] = "O nome parece combinar entidades. Revise o card ou reprocesse a fonte para confirmar."
                continue
            try:
                candidates = atlas_images.rank_candidates(search(node, attempt, options), label, options)
                issue = "Nenhuma imagem compatível nesta busca; nova tentativa agendada."
                for candidate in candidates[:8]:
                    if cancelled():
                        break
                    if any(change.get("url") == candidate["url"] and change["node_id"] != node_id
                           for change in task["changes"]):
                        continue
                    try:
                        image = approve(candidate)
                        if cancelled():
                            break
                        if not image.get("id"):
                            continue
                        changed = associate(node_id, image["id"], expected[node_id])
                        if changed:
                            task["changes"].append({"node_id": node_id, "media_id": image["id"],
                                "previous_media_id": expected[node_id], "url": candidate["url"],
                                "job_id": task["job_id"]})
                            task["filled"] = int(task.get("filled", 0)) + 1
                        actual = current_media(node_id)
                        if changed or (actual and is_valid_media(actual)):
                            done.add(node_id)
                            task["issues"].pop(node_id, None)
                        break
                    except Exception:
                        issue = "O arquivo não pôde ser baixado; tentando outras imagens."
                if node_id not in done:
                    task["issues"][node_id] = issue
            except Exception:
                task["issues"][node_id] = "A busca está indisponível; a fila continuará tentando."
            task.update(completed=len(done), remaining=len(by_id) - len(done), resolved_ids=sorted(done),
                        pending_ids=[key for key in by_id if key not in done])
            persist(task)
            if wait(1):
                break
        if cancelled():
            break
        if len(done) < len(by_id):
            seconds = min(300, 15 * 2 ** min(task["pass"] - 1, 5))
            task.update(phase="waiting_retry", next_retry_at=time.time() + seconds,
                        message=f"{len(by_id) - len(done)} imagem(ns) pendente(s). Novas buscas em {seconds}s.")
            persist(task)
            if wait(seconds):
                break
    task.update(phase="cancelled", next_retry_at=0, message="Preenchimento pausado. As imagens e a fila foram salvas.")
    persist(task)
