import shutil
import subprocess
from pathlib import Path

import pytest


def test_missable_details_pending_sources_and_navigation():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the UI behavior test')
    source = (Path(__file__).resolve().parents[1] / 'ui/app.js').read_text(encoding='utf-8')
    function = source.split('function showMissableDetails(game) {', 1)[1].split('\nfunction bindSidebar()', 1)[0]
    script = r'''
const assert=require('node:assert/strict');
let html, action, removed=false, rendered=false;
const S={};
const esc=s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;');
const toast=()=>{};
const renderDashboard=()=>{rendered=true};
const xpModal=(title,body)=>{html=body;return {remove:()=>{removed=true},querySelector:()=>({addEventListener:(event,fn)=>{action=fn}})}};
''' + 'function showMissableDetails(game) {' + function + r'''
showMissableDetails({pending_missables:[{name:'<Official>',desc:'Full requirement',earned:true},{name:'Finished',hardcore:true}],smart_guide:{progress:{completed:['done']},current:{chapters:[{title:'Chapter',blocks:[{id:'pending',type:'missable',title:'Guide warning',text:'Do this first'},{id:'done',type:'missable',title:'Completed warning'}]}]}}});
assert(html.includes('&lt;Official&gt;'));
assert(html.includes('Full requirement'));
assert(html.includes('Refazer em Hardcore'));
assert(html.includes('Etapa ainda não identificada'));
assert(html.includes('Guide warning'));
assert(!html.includes('Completed warning'));
assert(!html.includes('Finished'));
action(); assert(removed&&rendered); assert.equal(S.tab,'achievements');assert.equal(S.achievementFilter,'missable');
showMissableDetails({}); assert(html.includes('Nenhum perdível pendente'));
'''
    subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    assert 'data-open-missables aria-label="Ver detalhes dos perdíveis"' in source
    assert "addEventListener('click', () => showMissableDetails(S.dashboardGame))" in source
