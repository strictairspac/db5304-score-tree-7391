import {heading} from '../components/common/heading.js';
import {fields, readFields} from '../components/fields.js';
import {topic} from '../constants/project.js';
import {esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';
import {go} from '../router/hash.js';

function sampleFor(spec, mode) {
  const sample = mode === 'reconcile' ? spec.reconcile_sample : spec.sample;
  const copy = {...sample};
  delete copy.mode;
  return copy;
}

export function editorPage(state, identity) {
  if (state.session.role!=='operator') return heading(topic.modules[1], '经办员可以建立并提交业务档案。');
  const original = state.cases.find(row=>row.id===identity);
  const mode = original?.payload?.mode || 'inspect';
  if (original?.payload) {
    state.draft = {...original.payload};
    delete state.draft.mode;
  } else {
    state.draft = sampleFor(state.spec, mode);
  }
  const modes = state.spec.modes || {};
  const options = Object.entries(modes).map(([value,label])=>
    `<option value="${esc(value)}" ${value===mode?'selected':''}>${esc(label)}</option>`).join('');
  return `${heading(topic.modules[1], state.spec.guidance)}
    <form id="case-form"><label class="field title-field"><span>档案名称</span><input name="title" required maxlength="120" value="${esc((original?.title || state.spec.name) + ' · 复核')}" /></label>
    <label class="field mode-field"><span>执行模式</span><select id="mode-select">${options}</select></label>
    <div class="field-grid" id="field-grid">${fields(state.draft, state.spec.field_labels)}</div><div class="actions form-actions"><button>保存并提交执行</button><a class="secondary" href="#cases">返回档案</a></div></form>`;
}

export function bindEditor(state, api, fail) {
  const select = document.getElementById('mode-select');
  select?.addEventListener('change', ()=>{
    state.draft = sampleFor(state.spec, select.value);
    document.getElementById('field-grid').innerHTML = fields(state.draft, state.spec.field_labels);
  });
  submit('case-form', async data=>{
    const payload = readFields(state.draft, state.spec.field_labels);
    const mode = select?.value;
    if (mode) payload.mode = mode;
    const draft = await api('/cases', {method:'POST', data:{title:data.get('title'), payload}});
    try {
      const run = await api(`/cases/${draft.id}/runs`, {method:'POST'});
      go(`runs/${run.id}`);
    } catch (error) {
      go(`cases/${draft.id}`);
      fail(new Error(`档案已保存。提交执行失败：${error.message}`));
    }
  }, fail);
}
