from __future__ import annotations

from pathlib import Path


def replace_exact(path: str, old: str, new: str, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {found}: {old[:100]!r}")
    file.write_text(text.replace(old, new, count), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Mobile state: checkpoint is guide-scoped, and same-target offline intents
# are chained only after their predecessor has been confirmed.
# ---------------------------------------------------------------------------
replace_exact(
    "ui/companion/state.js",
    '''export function targetKey(values) {
  if (values.kind === "progress") return "progress:" + values.action + ":" + values.target.block_id;
  if (values.kind === "requirement") return "requirement:" + values.target.system_id + ":" + values.target.edge_id + ":" + values.target.requirement_id;
  if (values.kind === "goal") return "goal:" + values.target.system_id;
  if (values.kind === "item") return "item:" + values.target.item_id;
  return values.kind || "unknown";
}
''',
    '''export function targetKey(values) {
  if (values.kind === "progress") {
    // There is only one resume point per guide/game. Completion, favorite and
    // reveal remain block-scoped, but checkpoint writes share one version.
    if (values.action === "checkpoint") return "progress:checkpoint";
    return "progress:" + values.action + ":" + values.target.block_id;
  }
  if (values.kind === "requirement") return "requirement:" + values.target.system_id + ":" + values.target.edge_id + ":" + values.target.requirement_id;
  if (values.kind === "goal") return "goal:" + values.target.system_id;
  if (values.kind === "item") return "item:" + values.target.item_id;
  return values.kind || "unknown";
}

export function rebaseNextDependent(operations, completedIndex, target, valueVersion) {
  const version = Number(valueVersion);
  if (!Array.isArray(operations) || !Number.isInteger(version) || version < 0) return null;
  for (let index = completedIndex + 1; index < operations.length; index += 1) {
    const operation = operations[index];
    if (!operation || operation.target !== target || operation.status === "conflict") continue;
    return {
      index,
      operation: {
        ...operation,
        values: {...operation.values, _expectedValueVersion: version},
        body: {...operation.body, expected_value_version: version},
      },
    };
  }
  return null;
}
''',
)

replace_exact(
    "ui/companion/state.js",
    '''  if (result.kind === "progress") {
    const parts = target.split(":");
    const action = parts[1], blockId = parts[2];
    const map = {complete: "completed", favorite: "favorites", reveal: "revealed_spoilers"};
    if (action === "checkpoint") next.progress.checkpoint = result.value ? blockId : "";
''',
    '''  if (result.kind === "progress") {
    const parts = target.split(":");
    const action = parts[1], blockId = parts[2];
    const map = {complete: "completed", favorite: "favorites", reveal: "revealed_spoilers"};
    if (action === "checkpoint") next.progress.checkpoint = String(result.value || "");
''',
)

# ---------------------------------------------------------------------------
# Mobile transport: exact replay first; after ACK, advance only the next
# same-target intent. Checkpoint sends the absolute block id as desired value.
# ---------------------------------------------------------------------------
replace_exact(
    "ui/companion/app.js",
    'import {guideBlocks, mediaUrl, namespaceFor, nodeById, normalized, patchConfirmedValue, requestId, systemById, targetKey, versionFor} from "./state.js";',
    'import {guideBlocks, mediaUrl, namespaceFor, nodeById, normalized, patchConfirmedValue, rebaseNextDependent, requestId, systemById, targetKey, versionFor} from "./state.js";',
)

replace_exact(
    "ui/companion/app.js",
    '''async function flush() {
  if (!M.connected || !M.storage?.available || M.flushing) return M.flushing;
  M.flushing = (async () => {
    const blocked = new Set();
    for (const op of await M.storage.pending(M.namespace)) {
      if (op.status === "conflict") { blocked.add(op.target); continue; }
      if (blocked.has(op.target)) continue;
      try {
        const result = await api("/api/action", {...op.body, expected_value_version: versionFor(M.data, op.values)});
        M.data = patchConfirmedValue(M.data, result); await M.storage.remove(op.id); dropPending(op.target); notify("Alteração pendente salva no PC.");
      } catch (error) {
        if (error.value?.conflict) { await M.storage.update(op.id, {status: "conflict", server: error.value}); M.p.conflict ||= {id: op.id, target: op.target, values: op.values, body: op.body, server: error.value}; blocked.add(op.target); }
        else break;
      }
    }
    persist(); render();
  })();
  try { await M.flushing; } finally { M.flushing = null; }
}
''',
    '''async function flush() {
  if (!M.connected || !M.storage?.available || M.flushing) return M.flushing;
  M.flushing = (async () => {
    const blocked = new Set();
    const operations = await M.storage.pending(M.namespace);
    for (let index = 0; index < operations.length; index += 1) {
      const op = operations[index];
      if (op.status === "conflict") { blocked.add(op.target); continue; }
      if (blocked.has(op.target)) continue;
      try {
        // Replay the exact request first. A lost ACK is recovered through the
        // persistent receipt for this request_id; a newer PC value must not be
        // substituted before the server decides whether this intent conflicts.
        const result = await api("/api/action", op.body);
        M.data = patchConfirmedValue(M.data, result);
        await M.storage.remove(op.id);
        dropPending(op.target);

        // Successive changes on the same semantic target are dependent. Only
        // the next intent advances to this confirmed version. If it succeeds,
        // it advances the following one in turn.
        const dependent = rebaseNextDependent(operations, index, op.target, result.value_version);
        if (dependent) {
          operations[dependent.index] = dependent.operation;
          await M.storage.update(dependent.operation.id, {
            values: dependent.operation.values,
            body: dependent.operation.body,
          });
        }
        notify("Alteração pendente salva no PC.");
      } catch (error) {
        if (error.value?.conflict) { await M.storage.update(op.id, {status: "conflict", server: error.value}); M.p.conflict ||= {id: op.id, target: op.target, values: op.values, body: op.body, server: error.value}; blocked.add(op.target); }
        else break;
      }
    }
    persist(); render();
  })();
  try { await M.flushing; } finally { M.flushing = null; }
}
''',
)

replace_exact(
    "ui/companion/app.js",
    '''  if (["complete","favorite","reveal","checkpoint"].includes(a)) return submit({kind:"progress",action:a === "complete" ? "complete" : a === "favorite" ? "favorite" : a === "reveal" ? "reveal" : "checkpoint",value:a === "reveal" || a === "checkpoint" ? true : button.dataset.value === "true",target:{block_id:button.dataset.block}});''',
    '''  if (["complete","favorite","reveal","checkpoint"].includes(a)) {
    const action = a === "complete" ? "complete" : a === "favorite" ? "favorite" : a === "reveal" ? "reveal" : "checkpoint";
    const value = action === "checkpoint" ? button.dataset.block : action === "reveal" ? true : button.dataset.value === "true";
    return submit({kind:"progress",action,value,target:{block_id:button.dataset.block}});
  }''',
)

# ---------------------------------------------------------------------------
# Backend progress contract: one checkpoint target/version per guide and an
# absolute checkpoint value. Boolean checkpoint values remain accepted for
# compatibility with already-installed protocol-v2 clients.
# ---------------------------------------------------------------------------
replace_exact(
    "smart_guide.py",
    '''        if action not in {"complete", "favorite", "reveal", "checkpoint"} or not isinstance(value, bool):
            raise SmartGuideError("Comando de progresso inválido.")
        request_id = _clean_text(request_id, 120)
''',
    '''        if action not in {"complete", "favorite", "reveal", "checkpoint"}:
            raise SmartGuideError("Comando de progresso inválido.")
        if action == "checkpoint":
            if isinstance(value, str):
                desired_value = _clean_text(value, 100)
                if desired_value and desired_value != _clean_text(block_id, 100):
                    raise SmartGuideError("Ponto de retomada inválido.")
            elif isinstance(value, bool):
                # Compatibility with clients created before checkpoint became
                # an absolute guide-scoped value.
                desired_value = block_id if value else ""
            else:
                raise SmartGuideError("Ponto de retomada inválido.")
        else:
            if not isinstance(value, bool):
                raise SmartGuideError("Comando de progresso inválido.")
            desired_value = value
        request_id = _clean_text(request_id, 120)
''',
)

replace_exact(
    "smart_guide.py",
    '        state = self.progress(slug); target = f"progress:{action}:{block_id}"\n',
    '        state = self.progress(slug)\n        target = "progress:checkpoint" if action == "checkpoint" else f"progress:{action}:{block_id}"\n',
)
replace_exact(
    "smart_guide.py",
    '        previous = receipts.get(request_id); fingerprint = _json_hash({"target": target, "value": value})\n',
    '        previous = receipts.get(request_id); fingerprint = _json_hash({"target": target, "value": desired_value})\n',
)
replace_exact(
    "smart_guide.py",
    '''            key = {"complete": "completed", "favorite": "favorites", "reveal": "revealed_spoilers"}.get(action)
            actual = block_id in set(state.get(key) or []) if key else state.get("checkpoint") == block_id
            raise SmartGuideConflict("Esta marcação mudou no PC ou em outro celular.", target=target, value=actual, value_version=version)
        if action == "checkpoint": state["checkpoint"] = block_id
''',
    '''            key = {"complete": "completed", "favorite": "favorites", "reveal": "revealed_spoilers"}.get(action)
            actual = block_id in set(state.get(key) or []) if key else str(state.get("checkpoint") or "")
            raise SmartGuideConflict("Esta marcação mudou no PC ou em outro celular.", target=target, value=actual, value_version=version)
        if action == "checkpoint": state["checkpoint"] = desired_value
''',
)
replace_exact(
    "smart_guide.py",
    '''        version += 1; versions[target] = version; receipts[request_id] = {"fingerprint": fingerprint, "value": value, "value_version": version}
        state["value_versions"] = versions; state["receipts"] = dict(list(receipts.items())[-256:]); state["updated_at"] = _now()
        _atomic_json(self._path(slug, "progress.json"), state)
        return {"progress": state, "target": target, "value": value, "value_version": version,
''',
    '''        version += 1; versions[target] = version; receipts[request_id] = {"fingerprint": fingerprint, "value": desired_value, "value_version": version}
        state["value_versions"] = versions; state["receipts"] = dict(list(receipts.items())[-256:]); state["updated_at"] = _now()
        _atomic_json(self._path(slug, "progress.json"), state)
        return {"progress": state, "target": target, "value": desired_value, "value_version": version,
''',
)
replace_exact(
    "smart_guide.py",
    '''        if action in {"complete", "favorite", "reveal", "checkpoint"} and block_id:
            target = f"progress:{action}:{block_id}"
            versions = progress.setdefault("value_versions", {})
''',
    '''        if action in {"complete", "favorite", "reveal", "checkpoint"} and block_id:
            target = "progress:checkpoint" if action == "checkpoint" else f"progress:{action}:{block_id}"
            versions = progress.setdefault("value_versions", {})
''',
)

# ---------------------------------------------------------------------------
# Remembered devices: versioned local schema, stable operation identity bound
# to installation/account/device/game, and canonical mDNS pairing origin.
# ---------------------------------------------------------------------------
replace_exact(
    "companion.py",
    "from flask import Flask, jsonify, request, send_file, send_from_directory",
    "from flask import Flask, g, jsonify, request, send_file, send_from_directory",
)

replace_exact(
    "companion.py",
    '''    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companion_devices (
                    device_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_companion_device_token ON companion_devices(token_hash)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companion_meta (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    def _connection(self):
''',
    '''    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self):
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companion_meta (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            row = conn.execute("SELECT value FROM companion_meta WHERE name = 'schema_version'").fetchone()
            try:
                version = int(row[0]) if row else 0
            except (TypeError, ValueError):
                version = 0
            if version > self.SCHEMA_VERSION:
                raise RuntimeError("Cadastro de aparelhos foi criado por uma versão mais nova do DigiTracker.")
            if version < 1:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS companion_devices (
                        device_id TEXT PRIMARY KEY,
                        account_id TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        seen_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        revoked_at REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_companion_device_token ON companion_devices(token_hash)")
                conn.execute(
                    "INSERT INTO companion_meta(name, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    (str(self.SCHEMA_VERSION),),
                )

    def _connection(self):
''',
)

replace_exact(
    "companion.py",
    '''    def _account(self):
        return str(self.account_provider() or "local")[:200]

    def _allowed_hosts(self):
''',
    '''    def _account(self):
        return str(self.account_provider() or "local")[:200]

    def _scoped_request_id(self, session, slug, request_id):
        raw_id = str(request_id or "").strip()
        if not raw_id:
            return ""
        device = str((session or {}).get("device_id") or (session or {}).get("id") or "temporary")
        scope = "|".join((self.installation_id, self._account(), device, str(slug or ""), raw_id))
        return hashlib.sha256(scope.encode("utf-8")).hexdigest()

    def _allowed_hosts(self):
''',
)

replace_exact(
    "companion.py",
    '''                    if not session or session["expires"] < now or session.get("account_id") != self._account():
                        return jsonify(ok=False, error="Conexão encerrada. Faça o pareamento novamente."), 401
                    session["seen_at"] = now
''',
    '''                    if not session or session["expires"] < now or session.get("account_id") != self._account():
                        return jsonify(ok=False, error="Conexão encerrada. Faça o pareamento novamente."), 401
                    session["seen_at"] = now
                    g.companion_session = dict(session)
''',
)

replace_exact(
    "companion.py",
    '''        @app.post("/api/action")
        def action():
            body = request.get_json()
            if not isinstance(body, dict):
                return jsonify(ok=False, error="Comando inválido."), 400
            try:
                result = self.command(body)
                return jsonify(result), (409 if result.get("conflict") else 200)
''',
    '''        @app.post("/api/action")
        def action():
            body = request.get_json()
            if not isinstance(body, dict):
                return jsonify(ok=False, error="Comando inválido."), 400
            body = dict(body)
            client_request_id = str(body.get("request_id") or "").strip()
            if client_request_id:
                body["request_id"] = self._scoped_request_id(
                    getattr(g, "companion_session", {}), body.get("slug") or "", client_request_id)
            try:
                result = self.command(body)
                if client_request_id and isinstance(result, dict) and result.get("request_id") == body.get("request_id"):
                    result = dict(result)
                    result["request_id"] = client_request_id
                return jsonify(result), (409 if result.get("conflict") else 200)
''',
)

replace_exact(
    "companion.py",
    '''            if include_qr and self.pair_code:
                import qrcode
                stream = io.BytesIO()
                qrcode.make(result["url"] + "/#pair=" + self.pair_code).save(stream, format="PNG")
                result["qr"] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
''',
    '''            if include_qr and self.pair_code:
                import qrcode
                stream = io.BytesIO()
                pair_base = result.get("canonical_url") or result["url"]
                result["pairing_url"] = pair_base + "/#pair=" + self.pair_code
                qrcode.make(result["pairing_url"]).save(stream, format="PNG")
                result["qr"] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
''',
)

# ---------------------------------------------------------------------------
# Regression tests for the contracts above.
# ---------------------------------------------------------------------------
smart_test = Path("tests/test_smart_guide.py")
smart_text = smart_test.read_text(encoding="utf-8")
if "test_checkpoint_version_is_scoped_to_the_guide" not in smart_text:
    smart_text += '''\n\ndef test_checkpoint_version_is_scoped_to_the_guide(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    store.ensure_source("jogo", "Jogo", SECTIONS)
    blocks = [block["id"] for chapter in store.current("jogo")["chapters"] for block in chapter["blocks"]]
    first, second = blocks[:2]

    saved = store.update_progress_versioned(
        "jogo", "checkpoint", first, first,
        request_id="phone-a-1", expected_value_version=0,
    )
    assert saved["target"] == "progress:checkpoint"
    assert saved["value"] == first
    assert saved["value_version"] == 1

    with pytest.raises(smart_guide.SmartGuideConflict) as conflict:
        store.update_progress_versioned(
            "jogo", "checkpoint", second, second,
            request_id="phone-a-2", expected_value_version=0,
        )
    assert conflict.value.target == "progress:checkpoint"
    assert conflict.value.value == first
    assert conflict.value.value_version == 1

    moved = store.update_progress_versioned(
        "jogo", "checkpoint", second, second,
        request_id="phone-a-3", expected_value_version=1,
    )
    assert moved["value"] == second
    assert store.progress("jogo")["checkpoint"] == second
'''
    smart_test.write_text(smart_text, encoding="utf-8", newline="\n")

experience_test = Path("tests/test_experience.py")
experience_text = experience_test.read_text(encoding="utf-8")
if "test_companion_request_identity_is_scoped_to_device_account_and_game" not in experience_text:
    experience_text += '''\n\ndef test_companion_request_identity_is_scoped_to_device_account_and_game(server):
    a = {"id": "session-a", "device_id": "phone-a"}
    b = {"id": "session-b", "device_id": "phone-b"}
    first = server._scoped_request_id(a, "game-a", "same-client-id")
    assert first == server._scoped_request_id(a, "game-a", "same-client-id")
    assert first != server._scoped_request_id(b, "game-a", "same-client-id")
    assert first != server._scoped_request_id(a, "game-b", "same-client-id")


def test_companion_registry_records_schema_version(tmp_path):
    registry = companion.DeviceRegistry(tmp_path / "companion.sqlite3")
    with registry._connection() as conn:
        row = conn.execute("SELECT value FROM companion_meta WHERE name = 'schema_version'").fetchone()
    assert row and int(row[0]) == companion.DeviceRegistry.SCHEMA_VERSION
'''
    experience_test.write_text(experience_text, encoding="utf-8", newline="\n")

replace_exact(
    "tests/companion_ui_smoke.cjs",
    '''      return {fallbackId, firstVersion, replayVersion};
    });
''',
    '''      const operations = [
        {id: "one", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "two", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "three", target: "item:x", status: "pending", values: {_expectedValueVersion: 4}, body: {expected_value_version: 4}},
      ];
      const rebased = state.rebaseNextDependent(operations, 0, "progress:checkpoint", 1);
      const checkpointTarget = state.targetKey({kind: "progress", action: "checkpoint", target: {block_id: "block-a"}});
      return {fallbackId, firstVersion, replayVersion, rebased, checkpointTarget};
    });
''',
)
replace_exact(
    "tests/companion_ui_smoke.cjs",
    '''    assert.equal(stateChecks.replayVersion, 2,
      "Queued operations must keep their original expected version so reconnect exposes a conflict instead of overwriting PC state");
''',
    '''    assert.equal(stateChecks.replayVersion, 2,
      "Queued operations must keep their original expected version so reconnect exposes a conflict instead of overwriting PC state");
    assert.equal(stateChecks.checkpointTarget, "progress:checkpoint", "Checkpoint concurrency must be guide-scoped");
    assert.equal(stateChecks.rebased.index, 1, "Only the next dependent operation should be rebased");
    assert.equal(stateChecks.rebased.operation.body.expected_value_version, 1,
      "The next same-target offline intent must advance from the version confirmed by its predecessor");
''',
)

print("Mobile E protocol continuation applied.")
