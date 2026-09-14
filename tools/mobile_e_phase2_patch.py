from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise RuntimeError(f"{path}: expected {count}, found {found}: {old[:110]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Snapshot revisions. Polling can now ask for small revision tokens and fetch
# only the public resource that changed.
# ---------------------------------------------------------------------------
replace(
    "experience_api.py",
    '''        with self._companion_ai_lock:
            answer = copy.deepcopy(self._companion_ai.get(slug) or {})
        return {"ok": True, "api_version": 2, "version": version,
                "definition_revision": definition_revision, "content_revision": content_revision,
                "progress_revision": progress_revision,
                "game": {key: game.get(key) for key in ("slug", "title", "platform", "art", "mastery", "completion")},
                "games": games_public,
                "chapters": document.get("chapters") or [], "systems": systems,
                "media": [{"id": item.get("id"), "url": item.get("url"), "title": item.get("title")} for item in bundle.get("media") or [] if item.get("status") != "rejected"],
                "progress": progress_public,
                "system_state": public_system_state, "objective": next_objective,
                "items": items, "items_revision": items_revision,
                "missables": game.get("pending_missables") or [], "achievements": game.get("achievements") or [], "answer": answer}
''',
    '''        with self._companion_ai_lock:
            answer = copy.deepcopy(self._companion_ai.get(slug) or {})
        chapters = document.get("chapters") or []
        media = [{"id": item.get("id"), "url": item.get("url"), "title": item.get("title")}
                 for item in bundle.get("media") or [] if item.get("status") != "rejected"]
        achievements = copy.deepcopy(game.get("achievements") or [])
        game_public = {key: game.get(key) for key in ("slug", "title", "platform", "art", "mastery", "completion")}
        revisions = {
            "guide": smart_guide._json_hash([definition_revision, chapters, progress_public, next_objective]),
            "atlas": smart_guide._json_hash([definition_revision, systems, public_system_state]),
            "items": smart_guide._json_hash([items_revision, items]),
            "media": smart_guide._json_hash(media),
            "achievements": smart_guide._json_hash(achievements),
            "assistant": smart_guide._json_hash(answer),
            "games": smart_guide._json_hash([games_public, self._active_slug]),
        }
        return {"ok": True, "api_version": 2, "version": version,
                "definition_revision": definition_revision, "content_revision": content_revision,
                "progress_revision": progress_revision, "revisions": revisions,
                "active_pc_slug": self._active_slug,
                "game": game_public, "games": games_public,
                "chapters": chapters, "systems": systems, "media": media,
                "progress": progress_public,
                "system_state": public_system_state, "objective": next_objective,
                "items": items, "items_revision": items_revision,
                "missables": game.get("pending_missables") or [], "achievements": achievements, "answer": answer}
''',
)

# ---------------------------------------------------------------------------
# Remembered-device restoration reports an explicit reason. This lets the
# browser distinguish invalid authorization from a transient network failure.
# ---------------------------------------------------------------------------
replace(
    "companion.py",
    '''    def restore(self, token, account_id, now=None):
        now = time.time() if now is None else now
        with self._connection() as conn:
            row = conn.execute("""
                SELECT device_id, account_id, name, created_at, seen_at, expires_at, revoked_at
                FROM companion_devices WHERE token_hash = ?
            """, (_digest(token),)).fetchone()
            if not row:
                return None
            item = dict(zip(("id", "account_id", "name", "created_at", "seen_at", "expires_at", "revoked_at"), row))
            if item["account_id"] != str(account_id) or item["revoked_at"] is not None or item["expires_at"] < now:
                return None
            expires_at = now + 90 * 86400
            conn.execute("UPDATE companion_devices SET seen_at = ?, expires_at = ? WHERE device_id = ?",
                         (now, expires_at, item["id"]))
            item.update({"seen_at": now, "expires_at": expires_at})
            return item
''',
    '''    def restore_status(self, token, account_id, now=None):
        now = time.time() if now is None else now
        if not token:
            return None, "missing_device"
        with self._connection() as conn:
            row = conn.execute("""
                SELECT device_id, account_id, name, created_at, seen_at, expires_at, revoked_at
                FROM companion_devices WHERE token_hash = ?
            """, (_digest(token),)).fetchone()
            if not row:
                return None, "unknown_device"
            item = dict(zip(("id", "account_id", "name", "created_at", "seen_at", "expires_at", "revoked_at"), row))
            if item["account_id"] != str(account_id):
                return None, "account_changed"
            if item["revoked_at"] is not None:
                return None, "revoked"
            if item["expires_at"] < now:
                return None, "expired"
            expires_at = now + 90 * 86400
            conn.execute("UPDATE companion_devices SET seen_at = ?, expires_at = ? WHERE device_id = ?",
                         (now, expires_at, item["id"]))
            item.update({"seen_at": now, "expires_at": expires_at})
            return item, "ok"

    def restore(self, token, account_id, now=None):
        return self.restore_status(token, account_id, now)[0]
''',
)

replace(
    "companion.py",
    '''                if len(queue) >= (90 if key[1] == "pair" else 300):
                    return jsonify(ok=False, error="Aguarde antes de tentar novamente."), 429
''',
    '''                if len(queue) >= (90 if key[1] == "pair" else 300):
                    response = jsonify(ok=False, error="Aguarde antes de tentar novamente.")
                    response.status_code = 429
                    response.headers["Retry-After"] = "1"
                    return response
''',
)

replace(
    "companion.py",
    '''        @app.post("/session/restore")
        def restore_session():
            device_token = request.cookies.get("dt_device", "")
            item = self.registry.restore(device_token, self._account()) if device_token else None
            if not item:
                response = jsonify(ok=False, needs_pairing=True, error="Aparelho não autorizado. Leia um novo QR Code.")
                response.delete_cookie("dt_session")
                response.delete_cookie("dt_device")
                return response, 401
            with self.lock:
                token = self._new_session(item)
            response = jsonify(ok=True, restored=True, device={"id": item["id"], "name": item["name"]})
            response.set_cookie("dt_session", token, httponly=True, samesite="Strict", max_age=86400)
            return response
''',
    '''        @app.post("/session/restore")
        def restore_session():
            device_token = request.cookies.get("dt_device", "")
            item, reason = self.registry.restore_status(device_token, self._account())
            if not item:
                response = jsonify(ok=False, needs_pairing=True, code=reason,
                                   error="Aparelho não autorizado. Leia um novo QR Code.")
                response.delete_cookie("dt_session")
                response.delete_cookie("dt_device")
                return response, 401
            with self.lock:
                token = self._new_session(item)
            response = jsonify(ok=True, restored=True, device={"id": item["id"], "name": item["name"]})
            response.set_cookie("dt_session", token, httponly=True, samesite="Strict", max_age=86400)
            return response
''',
)

# Light revision and changed-resource projections.
replace(
    "companion.py",
    '''        def bounded_int(name, default, minimum, maximum):
            try:
                return max(minimum, min(maximum, int(request.args.get(name, default))))
            except (TypeError, ValueError):
                return default

        @app.get("/api/games")
        def games():
            value, error = public_state()
            if error:
                return error
            # A companion session is scoped to the PC's active library.  A
            # slug query may select another permitted game for consultation,
            # but it never changes the compact game's active session.
            return jsonify(ok=True, games=[value.get("game") or {}],
                           active_slug=(value.get("game") or {}).get("slug", ""))
''',
    '''        def bounded_int(name, default, minimum, maximum):
            try:
                return max(minimum, min(maximum, int(request.args.get(name, default))))
            except (TypeError, ValueError):
                return default

        @app.get("/api/revisions")
        def revisions():
            value, error = public_state()
            if error:
                return error
            return jsonify(ok=True, revisions=value.get("revisions") or {},
                           active_pc_slug=value.get("active_pc_slug", ""),
                           selected_slug=(value.get("game") or {}).get("slug", ""))

        @app.get("/api/media")
        def media_state():
            value, error = public_state()
            if error:
                return error
            return jsonify(ok=True, media=value.get("media") or [],
                           revision=(value.get("revisions") or {}).get("media", ""))

        @app.get("/api/assistant")
        def assistant_state():
            value, error = public_state()
            if error:
                return error
            return jsonify(ok=True, answer=value.get("answer") or {},
                           revision=(value.get("revisions") or {}).get("assistant", ""))

        @app.get("/api/games")
        def games():
            value, error = public_state()
            if error:
                return error
            # Selecting a slug changes only this mobile projection. The PC's
            # active compact game is reported separately and never mutated.
            return jsonify(ok=True, games=value.get("games") or [],
                           active_slug=value.get("active_pc_slug", ""),
                           selected_slug=(value.get("game") or {}).get("slug", ""),
                           revision=(value.get("revisions") or {}).get("games", ""))
''',
)

replace(
    "companion.py",
    '''            return jsonify(ok=True, chapters=chapters,
                           progress=value.get("progress") or {},
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))
''',
    '''            return jsonify(ok=True, chapters=chapters,
                           progress=value.get("progress") or {}, objective=value.get("objective") or {},
                           revision=(value.get("revisions") or {}).get("guide", ""),
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))
''',
)

replace(
    "companion.py",
    '''            return jsonify(ok=True, systems=value.get("systems") or [],
                           system_state=value.get("system_state") or {},
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))
''',
    '''            return jsonify(ok=True, systems=value.get("systems") or [],
                           system_state=value.get("system_state") or {},
                           revision=(value.get("revisions") or {}).get("atlas", ""),
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))
''',
)

# ---------------------------------------------------------------------------
# Mobile client: Retry-After, explicit restore state, lightweight polling and
# selective fetches. Full state remains the initial/bootstrap reconciliation.
# ---------------------------------------------------------------------------
replace(
    "ui/companion/app.js",
    '''const M = {data: null, storage: null, namespace: "", loadedNamespace: "", connected: false, refreshing: false, retry: 0, retryTimer: 0, flushing: null, message: "", error: false, drafts: {question: "", items: {}}, p: defaults()};''',
    '''const M = {data: null, revisions: {}, storage: null, namespace: "", loadedNamespace: "", connected: false, refreshing: false, retry: 0, retryTimer: 0, flushing: null, message: "", error: false, drafts: {question: "", items: {}}, p: defaults()};''',
)

replace(
    "ui/companion/app.js",
    '''  return {view: "home", history: [], gameSlug: "", guideMode: "read", guideBlockId: "", atlasMode: "systems", atlasQuery: "", atlasSystemId: "", atlasNodeId: "", atlasEdgeId: "", itemFilter: "all", itemQuery: "", itemId: "", search: false, query: "", library: false, moreMode: "menu", assistant: false, source: null, conflict: null, recent: [], pending: []};''',
    '''  return {view: "home", history: [], gameSlug: "", guideMode: "read", guideBlockId: "", atlasMode: "systems", atlasQuery: "", atlasSystemId: "", atlasNodeId: "", atlasEdgeId: "", itemFilter: "all", itemQuery: "", itemId: "", search: false, query: "", library: false, moreMode: "menu", assistant: false, source: null, conflict: null, forget: null, recent: [], pending: []};''',
)

replace(
    "ui/companion/app.js",
    '''  saveTimer = setTimeout(() => { const value = globalThis.structuredClone ? structuredClone(M.p) : JSON.parse(JSON.stringify(M.p)); delete value.pending; M.storage.setPresentation(M.namespace, value).catch(() => notify("Não foi possível guardar a navegação neste aparelho.", true)); }, 120);''',
    '''  saveTimer = setTimeout(() => { const value = globalThis.structuredClone ? structuredClone(M.p) : JSON.parse(JSON.stringify(M.p)); delete value.pending; delete value.forget; M.storage.setPresentation(M.namespace, value).catch(() => notify("Não foi possível guardar a navegação neste aparelho.", true)); }, 120);''',
)

replace(
    "ui/companion/app.js",
    '''async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin"});
  let value = {}; try { value = await response.json(); } catch (_) {}
  if (!response.ok || value.ok === false) { const error = new Error(value.error || "Não foi possível falar com o DigiTracker."); error.status = response.status; error.value = value; throw error; }
  return value;
}
async function restoreSession() { try { await api("/session/restore", {}); return true; } catch (_) { return false; } }
function retry() {
  clearTimeout(M.retryTimer); const seconds = [1,2,4,8,15,30][Math.min(M.retry++, 5)];
  M.retryTimer = setTimeout(() => refresh(false), seconds * 1000 + Math.floor(Math.random() * 250));
}
async function refresh(draw = true) {
  if (M.refreshing || document.hidden) return; M.refreshing = true;
  try {
    const response = await fetch(`/api/state${slug() ? `?slug=${encodeURIComponent(slug())}` : ""}`, {credentials: "same-origin"});
    let value = {}; try { value = await response.json(); } catch (_) {}
    if (response.status === 401 && await restoreSession()) { M.refreshing = false; return refresh(draw); }
    if (!response.ok || !value.ok) throw new Error(value.error || "Conexão indisponível.");
    const ns = namespaceFor(value, slug() || value.game?.slug); const changed = ns !== M.namespace;
    M.data = value; M.namespace = ns; M.retry = 0; setConnected(true);
    if (changed) await restorePresentation(); selections();
    if (draw) render(); flush().catch(() => {});
  } catch (error) { setConnected(false); if (draw && !M.data) disconnected(error.message); retry(); }
  finally { M.refreshing = false; }
}
''',
    '''function retryAfterSeconds(response) {
  const raw = response?.headers?.get?.("Retry-After");
  if (!raw) return 0;
  const numeric = Number(raw);
  if (Number.isFinite(numeric) && numeric >= 0) return Math.min(60, numeric);
  const date = Date.parse(raw);
  return Number.isFinite(date) ? Math.max(0, Math.min(60, (date - Date.now()) / 1000)) : 0;
}
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type":"application/json"}, body: body === undefined ? undefined : JSON.stringify(body), credentials:"same-origin"});
  let value = {}; try { value = await response.json(); } catch (_) {}
  if (!response.ok || value.ok === false) { const error = new Error(value.error || "Não foi possível falar com o DigiTracker."); error.status=response.status; error.value=value; error.retryAfter=retryAfterSeconds(response); throw error; }
  return value;
}
async function restoreSession() {
  try { await api("/session/restore", {}); return "restored"; }
  catch (error) { return error.status === 401 && error.value?.needs_pairing ? "needs-pairing" : "temporary"; }
}
function retry(minimum = 0) {
  clearTimeout(M.retryTimer); const backoff=[1,2,4,8,15,30][Math.min(M.retry++,5)];
  const seconds=Math.max(Number(minimum)||0,backoff);
  M.retryTimer=setTimeout(()=>M.data?poll(true):refresh(true),seconds*1000+Math.floor(Math.random()*250));
}
function query(path) { const mark=path.includes("?")?"&":"?"; return `${path}${mark}slug=${encodeURIComponent(slug())}`; }
async function paged(path, key) {
  const rows=[]; let offset=0, total=0;
  do { const response=await api(query(`${path}?offset=${offset}&limit=100`)); const page=response[key]||[]; rows.push(...page); total=Number(response.total??rows.length); offset+=page.length; if(!page.length)break; } while(rows.length<total);
  return rows;
}
function scrubPresentation() {
  const visibleGuide=new Set(guideBlocks(M.data).filter(row=>!row.hidden).map(row=>row.id));
  const visibleAtlas=new Set((M.data?.systems||[]).flatMap(system=>(system.nodes||[]).map(node=>node.id)));
  const visibleItems=new Set((M.data?.items||[]).map(item=>item.id));
  M.p.recent=(M.p.recent||[]).filter(row=>row.kind==="guide"?visibleGuide.has(row.id):row.kind==="atlas"?visibleAtlas.has(row.id):row.kind==="item"?visibleItems.has(row.id):false).slice(0,5);
}
async function refresh(draw = true) {
  if (M.refreshing || document.hidden) return; M.refreshing=true;
  try {
    const response=await fetch(`/api/state${slug()?`?slug=${encodeURIComponent(slug())}`:""}`,{credentials:"same-origin"});
    let value={}; try{value=await response.json();}catch(_){}
    if(response.status===401){const restored=await restoreSession();if(restored==="restored"){M.refreshing=false;return refresh(draw);}if(restored==="needs-pairing"){setConnected(false);if(draw)disconnected("Este aparelho precisa ser pareado novamente.");return;} }
    if(!response.ok||!value.ok){const error=new Error(value.error||"Conexão indisponível.");error.retryAfter=retryAfterSeconds(response);throw error;}
    const ns=namespaceFor(value,slug()||value.game?.slug), changed=ns!==M.namespace;
    M.data=value;M.revisions={...(value.revisions||{})};M.namespace=ns;M.retry=0;setConnected(true);
    if(changed)await restorePresentation();selections();scrubPresentation();
    if(draw)render();flush().catch(()=>{});
  } catch(error){setConnected(false);if(draw&&!M.data)disconnected(error.message);retry(error.retryAfter);}
  finally{M.refreshing=false;}
}
async function poll(draw = true) {
  if(M.refreshing||document.hidden||!M.data)return;M.refreshing=true;
  try {
    let revisionState;
    try { revisionState=await api(query("/api/revisions")); }
    catch(error){if(error.status===401){const restored=await restoreSession();if(restored==="restored"){M.refreshing=false;return poll(draw);}if(restored==="needs-pairing"){setConnected(false);if(draw)disconnected("Este aparelho precisa ser pareado novamente.");return;}}throw error;}
    const next=revisionState.revisions||{}, changed=Object.keys(next).filter(key=>next[key]!==M.revisions[key]);
    if(changed.includes("guide")){const r=await api(query("/api/guide"));M.data.chapters=r.chapters||[];M.data.progress=r.progress||{};M.data.objective=r.objective||{};}
    if(changed.includes("atlas")){const r=await api(query("/api/atlas/systems"));M.data.systems=r.systems||[];M.data.system_state=r.system_state||{};}
    if(changed.includes("items"))M.data.items=await paged("/api/items","items");
    if(changed.includes("media")){const r=await api(query("/api/media"));M.data.media=r.media||[];}
    if(changed.includes("achievements"))M.data.achievements=await paged("/api/achievements","achievements");
    if(changed.includes("assistant")){const r=await api(query("/api/assistant"));M.data.answer=r.answer||{};}
    if(changed.includes("games")){const r=await api(query("/api/games"));M.data.games=r.games||[];M.data.active_pc_slug=r.active_slug||"";const selected=M.data.games.find(game=>game.slug===slug());if(selected)M.data.game=selected;}
    M.revisions={...next};M.data.revisions={...next};M.retry=0;setConnected(true);selections();scrubPresentation();
    if(draw&&changed.length)render();
    flush().catch(()=>{});
  }catch(error){setConnected(false);retry(error.retryAfter);}
  finally{M.refreshing=false;}
}
''',
)

# ---------------------------------------------------------------------------
# Forget-device workflow: pending changes require an explicit sync/discard
# choice instead of a binary browser confirm.
# ---------------------------------------------------------------------------
replace(
    "ui/companion/app.js",
    '''async function forget() {
  const rows = await M.storage?.pending(M.namespace) || [];
  if (rows.length && !confirm("Há alterações pendentes. Descartar as pendências e esquecer este aparelho?")) return;
  for (const row of rows) await M.storage.remove(row.id);
  try { await api("/api/device/forget", {}); M.connected = false; M.p.pending = []; notify("Aparelho esquecido. Leia um novo QR Code para reconectar."); disconnected("Este aparelho foi removido do DigiTracker."); } catch (error) { notify(error.message,true); }
}
''',
    '''async function forget(mode = "ask") {
  const rows=await M.storage?.pending(M.namespace)||[];
  if(rows.length&&mode==="ask"){M.p.forget={count:rows.length};return render();}
  if(mode==="cancel"){M.p.forget=null;return render();}
  if(rows.length&&mode==="sync"){
    if(!M.connected)return notify("Reconecte ao PC para sincronizar antes de esquecer este aparelho.",true);
    await flush();const remaining=await M.storage?.pending(M.namespace)||[];
    if(remaining.length){M.p.forget={count:remaining.length};notify("Ainda há pendências ou conflitos. Revise-os ou descarte antes de esquecer.",true);return render();}
  }
  if(mode==="discard")for(const row of rows)await M.storage.remove(row.id);
  const remaining=await M.storage?.pending(M.namespace)||[];
  if(remaining.length)return;
  try{await api("/api/device/forget",{});M.connected=false;M.p.pending=[];M.p.forget=null;notify("Aparelho esquecido. Leia um novo QR Code para reconectar.");disconnected("Este aparelho foi removido do DigiTracker.");}catch(error){notify(error.message,true);}
}
function forgetModal(){
  const row=M.p.forget;if(!row)return "";
  return `<div class="modal-backdrop"><section class="mobile-modal" role="dialog" aria-modal="true"><span class="eyebrow">APARELHO</span><h2>Há ${Number(row.count)||0} alteração(ões) pendente(s)</h2><p>Escolha se quer sincronizar com o PC antes de remover a autorização deste aparelho ou descartar apenas estas pendências locais.</p><div class="modal-actions"><button class="outline" data-act="forget-cancel">Cancelar</button><button class="outline" data-act="forget-discard">Descartar e esquecer</button><button class="primary" data-act="forget-sync">Sincronizar antes</button></div></section></div>`;
}
''',
)

replace(
    "ui/companion/app.js",
    '''  content.innerHTML = `<div class="mobile-screen">${body()}</div>${M.p.search ? search() : ""}${M.p.library ? library() : ""}${sourceModal()}${conflictModal()}`; setConnected(M.connected);''',
    '''  content.innerHTML = `<div class="mobile-screen">${body()}</div>${M.p.search ? search() : ""}${M.p.library ? library() : ""}${sourceModal()}${conflictModal()}${forgetModal()}`; setConnected(M.connected);''',
)
replace(
    "ui/companion/app.js",
    '''  if (a === "forget") return forget();
''',
    '''  if (a === "forget") return forget("ask");
  if (a === "forget-sync") return forget("sync");
  if (a === "forget-discard") return forget("discard");
  if (a === "forget-cancel") return forget("cancel");
''',
)
replace(
    "ui/companion/app.js",
    '''window.addEventListener("online",()=>refresh(true));window.addEventListener("focus",()=>refresh(false));document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh(true);});setInterval(()=>{if(M.connected&&!document.hidden)refresh(false);},3000);boot();''',
    '''window.addEventListener("online",()=>M.data?poll(true):refresh(true));window.addEventListener("focus",()=>M.data?poll(true):refresh(true));document.addEventListener("visibilitychange",()=>{if(!document.hidden)(M.data?poll(true):refresh(true));});setInterval(()=>{if(M.connected&&!document.hidden)poll(true);},3000);boot();''',
)

# Desktop companion panel exposes canonical mDNS URL when available and keeps
# the IP address visible as the documented fallback.
replace(
    "ui/experience.js",
    '''<p data-private>${esc(r.url||'')} · QR válido por 2 minutos</p>''',
    '''<p data-private>${esc(r.canonical_url||r.url||'')} · QR válido por 2 minutos${r.canonical_url&&r.url?`<br><small>Alternativa por IP: ${esc(r.url)}</small>`:''}</p>''',
)

# ---------------------------------------------------------------------------
# Tests for explicit restoration reasons and light selective endpoints.
# ---------------------------------------------------------------------------
path = Path("tests/test_experience.py")
text = path.read_text(encoding="utf-8")
if "test_companion_light_revisions_and_resource_endpoints" not in text:
    text += '''\n\ndef test_companion_light_revisions_and_resource_endpoints(tmp_path):
    snapshot = {
        "ok": True,
        "game": {"slug": "a", "title": "A"},
        "games": [{"slug": "a", "title": "A"}, {"slug": "b", "title": "B"}],
        "active_pc_slug": "b",
        "revisions": {"guide": "g1", "atlas": "a1", "items": "i1", "media": "m1", "achievements": "h1", "assistant": "q1", "games": "l1"},
        "chapters": [], "progress": {}, "objective": {}, "systems": [], "system_state": {},
        "items": [], "items_revision": 0,
        "media": [{"id": "cover", "url": "/assets/cover.png", "title": "Cover"}],
        "achievements": [], "answer": {"answer": "ok"},
    }
    service = companion.CompanionServer(tmp_path / "ui", tmp_path / "assets", lambda slug: dict(snapshot), lambda body: {"ok": True})
    service.host, service.port = "127.0.0.1", 8766
    client = client_for(service)
    pair(service, client)
    revisions = request(client, "/api/revisions?slug=a").json
    assert revisions["revisions"]["media"] == "m1" and revisions["active_pc_slug"] == "b"
    games = request(client, "/api/games?slug=a").json
    assert [row["slug"] for row in games["games"]] == ["a", "b"]
    assert games["active_slug"] == "b" and games["selected_slug"] == "a"
    assert request(client, "/api/media?slug=a").json["media"][0]["id"] == "cover"
    assert request(client, "/api/assistant?slug=a").json["answer"]["answer"] == "ok"


def test_remembered_device_restore_reports_explicit_reason(tmp_path):
    registry = companion.DeviceRegistry(tmp_path / "companion.sqlite3")
    assert registry.restore_status("", "account")[1] == "missing_device"
    assert registry.restore_status("unknown", "account")[1] == "unknown_device"
    token = "remember-me"
    item = registry.remember("account", "Phone", token, now=100)
    assert registry.restore_status(token, "other", now=101)[1] == "account_changed"
    registry.revoke(item["id"], "account")
    assert registry.restore_status(token, "account", now=102)[1] == "revoked"
'''
    path.write_text(text, encoding="utf-8", newline="\n")

print("Mobile E phase 2 staged.")
