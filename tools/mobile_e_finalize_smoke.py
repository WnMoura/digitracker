from pathlib import Path

required = {
    "ui/companion/state.js": ["progress:checkpoint", "rebaseNextDependent"],
    "ui/companion/app.js": ["rebaseNextDependent", 'const result = await api("/api/action", op.body)'],
    "smart_guide.py": ['target = "progress:checkpoint" if action == "checkpoint"', "desired_value"],
    "companion.py": ["SCHEMA_VERSION = 1", "_scoped_request_id", "pairing_url"],
}
for path, markers in required.items():
    text = Path(path).read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            raise RuntimeError(f"{path}: staged continuation stopped before {marker!r}")

path = Path("tests/companion_ui_smoke.cjs")
text = path.read_text(encoding="utf-8")
if "checkpointTarget" not in text:
    anchor = '''      const patchedRequirement = state.patchConfirmedValue(requirementData, {
        ok: true,
        kind: "requirement",
        target: "requirement:system-a:edge-a:shared-condition",
        value: false,
        value_version: 3,
      });
'''
    addition = anchor + '''      const operations = [
        {id: "one", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "two", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "three", target: "item:x", status: "pending", values: {_expectedValueVersion: 4}, body: {expected_value_version: 4}},
      ];
      const rebased = state.rebaseNextDependent(operations, 0, "progress:checkpoint", 1);
      const checkpointTarget = state.targetKey({kind: "progress", action: "checkpoint", target: {block_id: "block-a"}});
'''
    if anchor not in text:
        raise RuntimeError("smoke: requirement anchor not found")
    text = text.replace(anchor, addition, 1)
    text = text.replace(
        '''        replayVersion,
        legacyRequirementKept:''',
        '''        replayVersion,
        checkpointTarget,
        rebased,
        legacyRequirementKept:''',
        1,
    )
    assertion_anchor = '''    assert.equal(stateChecks.replayVersion, 2,
      "Queued operations must keep their original expected version so reconnect exposes a conflict instead of overwriting PC state");
'''
    assertions = assertion_anchor + '''    assert.equal(stateChecks.checkpointTarget, "progress:checkpoint", "Checkpoint concurrency must be guide-scoped");
    assert.equal(stateChecks.rebased.index, 1, "Only the next dependent operation should be rebased");
    assert.equal(stateChecks.rebased.operation.body.expected_value_version, 1,
      "The next same-target offline intent must advance from the version confirmed by its predecessor");
'''
    if assertion_anchor not in text:
        raise RuntimeError("smoke: version assertion anchor not found")
    text = text.replace(assertion_anchor, assertions, 1)
    path.write_text(text, encoding="utf-8", newline="\n")

print("Mobile E smoke regression finalized.")
