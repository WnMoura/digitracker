from copy import deepcopy

import atlas_image_fill
import atlas_images


def test_query_respects_game_and_wiki_without_forcing_either():
    assert atlas_images.entity_query("Agumon*") == "Agumon"
    assert atlas_images.entity_query("Agumon", options={"context": "Digimon World 3", "wiki": "https://wikimon.net/Agumon"}) == "Digimon World 3 Agumon site:wikimon.net"


def test_ranking_rejects_substring_vs_video_and_wrong_wiki():
    results = [
        {"url": "https://cdn.test/Agumon.png", "source": "https://wikimon.net/Agumon", "title": "Agumon artwork"},
        {"url": "https://cdn.test/Agumon-Guilmon.jpg", "title": "Agumon vs Guilmon"},
        {"url": "https://cdn.test/Agumon2.png", "source": "https://wrong.test/Agumon", "title": "Agumon"},
        {"url": "https://cdn.test/BlackAgumon.png", "source": "https://wikimon.net/BlackAgumon", "title": "BlackAgumon"},
    ]
    assert atlas_images.rank_candidates(results, "Agumon", {"wiki": "wikimon.net"}) == results[:1]


def test_context_requires_evidence_of_the_selected_game():
    results = [
        {"url": "https://wikimon.net/Agumon_New_Century.png", "title": "Agumon New Century"},
        {"url": "https://wikimon.net/Agumon_DW3.png", "title": "Agumon"},
        {"url": "https://cdn.test/Agumon.png", "title": "Digimon World 3 Agumon"},
        {"url": "https://cdn.test/Agumon2.png", "title": "Digimon World 2 Agumon"},
    ]
    assert atlas_images.rank_candidates(results, "Agumon", {"context": "Digimon World 3"}) == [results[2], results[1]]


def test_search_operators_and_art_variants_are_not_title_terms():
    from web_image_search import _search_terms, rank_relevant
    assert _search_terms("Agumon artwork transparent png site:wikimon.net") == ["agumon"]
    item = {"url": "https://wikimon.net/Agumon.png", "title": "Agumon"}
    assert [value["url"] for value in rank_relevant([item], "Agumon site:wikimon.net")] == [item["url"]]


def test_source_illustration_uses_exact_editorial_entity_and_honors_wiki():
    source = {"url": "https://gamefaqs.gamespot.com/faqs/1", "structured": {"pages": [{"elements": [
        {"type": "image", "path": ["Forms", "Agumon"], "src": "https://cdn.test/1.png"},
        {"type": "figure", "path": ["Forms", "BlackAgumon"], "src": "https://cdn.test/2.png"},
    ]}]}}
    assert len(atlas_images.source_candidates(source, "Agumon")) == 1
    assert atlas_images.source_candidates(source, "Agumon", {"wiki": "wikimon.net"}) == []


def run_queue(search, *, initial=None, wait=None, approve=None, replace=False):
    media, states = dict(initial or {}), []
    task = {"options": atlas_images.normalize_options({"replace_existing": replace}), "job_id": "job", "filled": 0}
    nodes = [{"id": "a", "label": "Agumon", "card_number": 1}, {"id": "b", "label": "Guilmon", "card_number": 2}]
    def associate(node, value, expected):
        if media.get(node, "") != expected:
            return False
        media[node] = value
        return True
    atlas_image_fill.run(task, nodes=lambda: nodes, current_media=lambda key: media.get(key, ""),
        search=search, approve=approve or (lambda c: {"id": c["url"]}), associate=associate,
        cancelled=lambda: False, persist=lambda task: states.append(deepcopy(task)), wait=wait or (lambda _seconds: False))
    return task, states, media


def candidate(node):
    return {"url": f"https://cdn.test/{node['label']}.png", "title": node["label"]}


def test_queue_retries_missing_nodes_and_only_completes_at_full_coverage():
    queries = []
    def search(node, attempt, _options):
        queries.append((node["id"], attempt))
        return [] if node["id"] == "b" and attempt == 1 else [candidate(node)]
    task, states, media = run_queue(search)
    assert queries == [("a", 1), ("b", 1), ("b", 2)]
    assert any(state["phase"] == "waiting_retry" and state["remaining"] == 1 for state in states)
    assert task["phase"] == "complete" and task["completed"] == task["total"] == 2
    assert media.keys() == {"a", "b"}


def test_bad_download_tries_next_candidate_and_preserves_existing_images():
    def search(node, _attempt, _options):
        return [{**candidate(node), "url": "https://cdn.test/Guilmon-broken.png"}, candidate(node)]
    def approve(value):
        if "broken" in value["url"]:
            raise ValueError("not an image")
        return {"id": value["url"]}
    task, _states, media = run_queue(search, initial={"a": "manual"}, approve=approve)
    assert media["a"] == "manual" and task["filled"] == 1 and task["phase"] == "complete"


def test_cancelled_queue_is_not_reported_complete():
    task, states, _media = run_queue(lambda *_args: [], wait=lambda _seconds: True)
    assert task["phase"] == "cancelled" and task["completed"] == 0
    assert all(state["phase"] != "complete" for state in states)


def test_removed_image_is_requeued_even_if_previously_resolved():
    media = {"a": "old"}
    nodes = [{"id": "a", "label": "Agumon"}, {"id": "b", "label": "Guilmon"}]
    calls = []
    def search(node, attempt, _options):
        calls.append((node["id"], attempt))
        if node["id"] == "b" and attempt == 1:
            media.pop("a")
            return []
        return [candidate(node)]
    task = {"options": atlas_images.normalize_options(), "job_id": "job"}
    def associate(node, value, expected):
        assert media.get(node, "") == expected
        media[node] = value
        return True
    atlas_image_fill.run(task, nodes=lambda: nodes, current_media=lambda key: media.get(key, ""),
        search=search, approve=lambda c: {"id": c["url"]}, associate=associate,
        cancelled=lambda: False, persist=lambda _value: None, wait=lambda _seconds: False)
    assert calls == [("b", 1), ("a", 1), ("b", 2)]
    assert task["phase"] == "complete" and len(media) == 2


def test_exact_editorial_compound_name_is_searchable_but_a_list_is_not():
    assert atlas_images.searchable_label("Fire and Ice", known_labels=["Fire and Ice"])
    assert atlas_images.searchable_label("Knight, the Brave", known_labels=["Knight, the Brave"])
    assert not atlas_images.searchable_label("Agumon*, Guilmon", known_labels=["Agumon", "Guilmon"])


def test_missing_local_file_is_replaced_and_concurrent_manual_choice_wins():
    media = {"a": "missing-file"}
    task = {"options": atlas_images.normalize_options(), "job_id": "job", "resolved_ids": ["a"]}
    nodes = [{"id": "a", "label": "Agumon"}]
    def approve(_candidate):
        media["a"] = "manual-choice"
        return {"id": "downloaded"}
    def associate(node, value, expected):
        assert expected == "missing-file"
        if media[node] != expected:
            return False
        media[node] = value
        return True
    atlas_image_fill.run(task, nodes=lambda: nodes, current_media=lambda key: media.get(key, ""),
        search=lambda node, *_args: [candidate(node)], approve=approve, associate=associate,
        is_valid_media=lambda value: value != "missing-file",
        cancelled=lambda: False, persist=lambda _value: None, wait=lambda _seconds: False)
    assert task["phase"] == "complete" and media["a"] == "manual-choice"
    assert not task["changes"]
