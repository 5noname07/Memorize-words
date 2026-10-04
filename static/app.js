'use strict';
const $ = (selector) => document.querySelector(selector);
const state = {csrf: '', batches: [], words: [], mistakes: [], batchId: null, view: 'study', questions: {}, busy: {}, loading: {}, editorBusy: false};
let sessionVersion = 0, requests = new AbortController(), wordRequestVersion = 0;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let noticeTimer;
function notice(message, error = false) {
  $('#notice').textContent = message; $('#notice').classList.toggle('error', error); $('#notice').hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('#notice').hidden = true; }, 6000);
}
function loggedOut() {
  sessionVersion++; requests.abort(); requests = new AbortController();
  state.csrf = ''; state.questions = {}; state.batches = []; state.words = []; state.mistakes = []; state.batchId = null;
  $('#app-page').hidden = true; $('#login-page').hidden = false;
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  ['word-list','batch-list','mistake-list','history-list','study-weights','mistake-weights','stats'].forEach(id => { $(`#${id}`).replaceChildren(); });
  $('#word-panel').hidden = true; resetPractice('study'); resetPractice('mistakes');
}
async function api(path, method = 'GET', body) {
  const version = sessionVersion;
  const response = await fetch(`/api/${path}`, {method, signal: requests.signal, credentials: 'same-origin', headers: {
    'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf
  }, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  let result;
  try { result = await response.json(); } catch (error) { if (error.name === 'AbortError') throw error; throw new Error('服务器响应异常，请稍后重试'); }
  if (version !== sessionVersion) throw new DOMException('账户已变更', 'AbortError');
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') loggedOut();
    throw new Error(result.error || '操作失败');
  }
  return result;
}
function run(task) { return Promise.resolve().then(task).catch(error => { if (error.name !== 'AbortError') notice(error.message || '网络连接失败，请重试', true); }); }
function empty(message) { return `<p class="empty">${esc(message)}</p>`; }
function activeBatches() { return state.batches.filter(batch => !batch.archived); }
async function loadBatches() { state.batches = await api('batches'); renderBatches(); renderWeights(); }
function renderWeights() {
  for (const [selector, field, count] of [['#study-weights','study_weight','word_count'],['#mistake-weights','mistake_weight','mistake_count']]) {
    const batches = state.batches.filter(batch => field === 'study_weight' ? !batch.archived : batch.mistake_count > 0);
    $(selector).innerHTML = batches.length ? batches.map(batch => `<div class="weight-row"><div>${esc(batch.name)}${batch.archived ? '（已删除）' : ''}<small>${batch[count]} 个${count === 'word_count' ? '单词' : '错题'}</small></div><label><span class="sr-only" hidden>${esc(batch.name)}权重</span><input aria-label="${esc(batch.name)}${field === 'study_weight' ? '学习' : '错题'}权重" type="number" min="0" max="1000" step="0.1" value="${batch[field]}" data-weight="${field}" data-id="${batch.id}"></label></div>`).join('') : empty('暂无批次');
  }
}
function renderBatches() {
  const batches = activeBatches();
  $('#batch-list').innerHTML = batches.length ? batches.map(batch => `<article class="batch-card ${state.batchId === batch.id ? 'selected' : ''}"><h2>${esc(batch.name)}</h2><p class="fine">${batch.word_count} 个单词 · 学习权重 ${batch.study_weight}</p><div class="actions"><button data-action="open-batch" data-id="${batch.id}">查看单词</button><button data-action="edit-batch" data-id="${batch.id}">编辑</button><button class="quiet" data-action="delete-batch" data-id="${batch.id}">删除</button></div></article>`).join('') : empty('还没有批次。新建一个批次，再添加或批量导入单词。');
}
async function loadWords() {
  if (!state.batchId) return;
  const selectedId = state.batchId;
  const version = ++wordRequestVersion;
  const words = await api(`words?batch_id=${selectedId}&q=${encodeURIComponent($('#word-search').value)}`);
  if (selectedId !== state.batchId || version !== wordRequestVersion) return;
  state.words = words; $('#word-panel').hidden = false;
  $('#word-heading').textContent = state.batches.find(batch => batch.id === selectedId)?.name || '单词';
  $('#word-list').innerHTML = words.length ? words.map(word => `<div class="word-row"><div class="word-main"><strong>${esc(word.english)}</strong><p>${esc(word.phonetic)} · ${esc(word.meaning)}</p><div class="word-meta">首次导入批次：${esc(word.original_batch_name)}</div></div><div class="actions"><button data-action="edit-word" data-id="${word.id}">编辑 / 移动</button><button class="quiet" data-action="delete-word" data-id="${word.id}">删除</button></div></div>`).join('') : empty('此批次暂无匹配单词。');
}
async function loadMistakes() {
  state.mistakes = await api('mistakes');
  const oldFilter = $('#mistake-filter').value;
  const groups = [...new Map(state.mistakes.map(word => [word.original_batch_id, word.original_batch_name])).entries()];
  $('#mistake-filter').innerHTML = '<option value="">全部批次</option>' + groups.map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join('');
  if (groups.some(([id]) => String(id) === oldFilter)) $('#mistake-filter').value = oldFilter;
  renderMistakes();
}
function renderMistakes() {
  $('#mistake-count').textContent = `（${state.mistakes.length}）`;
  const filter = $('#mistake-filter').value;
  const words = state.mistakes.filter(word => !filter || String(word.original_batch_id) === filter);
  $('#mistake-list').innerHTML = words.length ? words.map(word => `<div class="word-row"><div class="word-main"><strong>${esc(word.english)}</strong><p>${esc(word.phonetic)} · ${esc(word.meaning)}</p><div class="word-meta">首次导入批次：${esc(word.original_batch_name)}<br>答错 ${word.wrong_count} 次 · 错题练习答对 ${word.correct_count} 次${word.deleted ? ' · 已从单词库删除' : ''}</div></div><button data-action="remove-mistake" data-id="${word.id}">移除</button></div>`).join('') : empty('暂无待复习的错题。正常学习中答错的单词会出现在这里。');
}
async function loadHistory() {
  const history = await api('history');
  $('#stats').innerHTML = [[history.total,'累计答题'],[history.correct,'正确答题'],[history.total ? `${Math.round(history.correct / history.total * 100)}%` : '—','正确率']].map(([value,label]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join('');
  $('#history-list').innerHTML = history.items.length ? history.items.map(item => `<div class="word-row"><div class="word-main"><strong>${esc(item.expected)}</strong><p>${esc(item.meaning)}</p><div class="word-meta">${item.mode === 'study' ? '正常学习' : '错题练习'} · ${esc(item.original_batch_name)}<br>${esc(item.created_at)} UTC · 你的答案：${esc(item.answer || '（空）')}</div></div><span class="${item.correct ? 'result-correct' : 'result-wrong'}">${item.correct ? '正确' : '错误'}</span></div>`).join('') : empty('开始练习后，你的答题记录会保存在这里。');
}
async function showView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach(button => { button.classList.toggle('active', button.dataset.view === view); button.setAttribute('aria-current', button.dataset.view === view ? 'page' : 'false'); });
  await loadBatches();
  if (view === 'batches' && state.batchId) await loadWords();
  if (view === 'mistakes') await loadMistakes();
  if (view === 'history') await loadHistory();
}
function resetPractice(mode) {
  const isStudy = mode === 'study';
  $(`#${mode}-card`).innerHTML = `<div class="practice-empty"><div class="card-mark">${isStudy ? 'Aa' : '↻'}</div><h2>${isStudy ? '从一个单词开始' : '把没记牢的再练一次'}</h2><p class="muted">${isStudy ? '按批次权重随机练习。' : '答对后仍保留在错题集，记牢后可手动移除。'}</p><button id="start-${mode}" class="primary" data-start="${mode}">${isStudy ? '开始学习' : '开始错题练习'}</button></div>`;
}
async function nextQuestion(mode) {
  if (state.loading[mode] || state.busy[mode]) return;
  state.loading[mode] = true;
  const card = $(`#${mode}-card`);
  card.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    const question = await api('question','POST',{mode}); state.questions[mode] = question;
    card.innerHTML = `<span class="pill ${mode === 'mistakes' ? 'warm' : ''}">${mode === 'study' ? '正常学习' : '错题练习'}</span><p class="question-label">首次导入批次：${esc(question.original_batch_name)}</p><h2 class="meaning">${esc(question.meaning)}</h2><p class="phonetic">${esc(question.phonetic)}</p><form data-answer="${mode}"><label>拼写英文<input class="answer-input" name="answer" autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false" maxlength="200" placeholder="输入英文单词" required></label><div class="answer-actions"><button class="primary" type="submit">检查答案</button><button type="button" data-next="${mode}">换一题</button></div></form><div class="feedback" hidden aria-live="polite"></div>`;
    card.querySelector('input').focus();
  } finally { state.loading[mode] = false; card.querySelectorAll('button').forEach(button => { button.disabled = false; }); }
}
async function submitAnswer(form) {
  const mode = form.dataset.answer;
  if (state.busy[mode]) return;
  state.busy[mode] = true; const button = form.querySelector('button'); button.disabled = true;
  try {
    const result = await api('answer','POST',{id:state.questions[mode].id, answer:form.elements.answer.value});
    form.elements.answer.disabled = true; form.querySelector('.answer-actions').hidden = true;
    const feedback = $(`#${mode}-card .feedback`); feedback.hidden = false; feedback.classList.toggle('wrong', !result.correct);
    feedback.innerHTML = `<p><strong>${result.correct ? '答对了！' : '再记一次，已记录到错题集。'}</strong></p><p>正确拼写：<strong>${esc(result.expected)}</strong></p><button class="primary" data-next="${mode}">下一题</button>`;
    feedback.querySelector('button').focus();
    await loadBatches(); if (mode === 'mistakes') await loadMistakes();
  } finally { state.busy[mode] = false; button.disabled = false; }
}
let editorSave;
function edit(title, fields, save) {
  $('#editor-title').textContent = title; $('#editor-fields').innerHTML = fields; editorSave = save;
  $('#editor').showModal(); $('#editor-fields input')?.focus();
}
function input(name, label, value = '', extra = '') { return `<label>${label}<input name="${name}" value="${esc(value)}" ${extra} required></label>`; }
function batchEditor(batch) {
  edit(batch ? '编辑批次' : '新建批次', input('name','批次名称',batch?.name || '', 'maxlength="80"') + input('study_weight','学习权重',batch?.study_weight ?? 1,'type="number" min="0" max="1000" step="0.1"') + input('mistake_weight','错题权重',batch?.mistake_weight ?? 1,'type="number" min="0" max="1000" step="0.1"'), async fields => {
    await api(batch ? `batches/${batch.id}` : 'batches', batch ? 'PATCH' : 'POST', fields);
    await loadBatches(); if (state.batchId) await loadWords(); notice('批次已保存');
  });
}
function wordEditor(word) {
  if (!state.batchId) return notice('请先选择一个批次',true);
  const options = activeBatches().map(batch => `<option value="${batch.id}" ${batch.id === (word?.batch_id || state.batchId) ? 'selected' : ''}>${esc(batch.name)}</option>`).join('');
  edit(word ? '编辑单词' : '添加单词', input('english','英文单词',word?.english || '', 'maxlength="100" autocapitalize="none" spellcheck="false"') + input('meaning','中文释义',word?.meaning || '', 'maxlength="500"') + input('phonetic','音标',word?.phonetic || '', 'maxlength="150"') + `<label>当前批次<select name="batch_id">${options}</select></label>` + (word ? `<p class="fine">首次导入批次：${esc(word.original_batch_name)}（不会随移动而改变）</p>` : ''), async fields => {
    fields.batch_id = Number(fields.batch_id); await api(word ? `words/${word.id}` : 'words', word ? 'PATCH' : 'POST',fields);
    await loadBatches(); await loadWords(); notice('单词已保存');
  });
}
function importEditor() {
  if (!state.batchId) return notice('请先选择一个批次',true);
  edit('批量导入单词', '<p class="fine">粘贴 CSV，或选择 UTF-8 CSV 文件。必须有 english,meaning,phonetic 表头，每次最多 5000 个词。包含逗号的释义请用双引号括住；校验失败时不会部分导入。</p><label>选择 CSV 文件<input id="csv-file" type="file" accept=".csv,text/csv"></label><label>CSV 内容<textarea name="csv" required placeholder="english,meaning,phonetic&#10;apple,苹果,/ˈæpəl/&#10;book,书,/bʊk/"></textarea></label>', async fields => {
    const result = await api('import','POST',{batch_id:state.batchId,csv:fields.csv});
    await loadBatches(); await loadWords(); notice(`已导入 ${result.count} 个单词`);
  });
  $('#csv-file').addEventListener('change', () => run(async () => {
    const file = $('#csv-file').files[0]; if (!file) return;
    if (file.size > 1000000) throw new Error('CSV 文件最多 1 MB');
    $('#editor-form').elements.csv.value = await file.text();
  }));
}
function confirmAction(message, action) {
  $('#confirm-message').textContent = message; $('#confirm-dialog').showModal();
  $('#confirm-yes').onclick = () => run(async () => {
    $('#confirm-yes').disabled = true;
    try { await action(); $('#confirm-dialog').close(); } finally { $('#confirm-yes').disabled = false; }
  });
}
$('#confirm-cancel').onclick = () => $('#confirm-dialog').close();
$('#close-editor').onclick = $('#cancel-editor').onclick = () => { if (!state.editorBusy) $('#editor').close(); };
$('#editor').addEventListener('cancel', event => { if (state.editorBusy) event.preventDefault(); });
$('#editor-form').addEventListener('submit', event => {
  event.preventDefault(); if (state.editorBusy) return;
  run(async () => {
    state.editorBusy = true; const submit = $('#editor-form button[type="submit"]'); submit.disabled = true;
    try { await editorSave(Object.fromEntries(new FormData(event.target))); $('#editor').close(); } finally { state.editorBusy = false; submit.disabled = false; }
  });
});
$('#login-form').addEventListener('submit', event => {
  event.preventDefault(); const button = event.target.querySelector('button'); if (button.disabled) return;
  run(async () => { button.disabled = true; try {
    const user = await api('login','POST',Object.fromEntries(new FormData(event.target)));
    event.target.reset(); state.csrf = user.csrf; $('#username').textContent = user.username;
    $('#login-page').hidden = true; $('#app-page').hidden = false; await showView('study');
  } finally { button.disabled = false; } });
});
$('#logout').onclick = () => run(async () => { await api('logout','POST',{}); loggedOut(); notice('已退出，学习数据已保留'); });
$('#new-batch').onclick = () => batchEditor(); $('#new-word').onclick = () => wordEditor(); $('#import-words').onclick = importEditor;
$('#start-study').onclick = () => run(() => nextQuestion('study'));
$('#start-mistakes').onclick = () => run(() => nextQuestion('mistakes'));
$('#word-search').addEventListener('input', () => run(loadWords));
$('#mistake-filter').addEventListener('change', renderMistakes);
document.addEventListener('submit', event => { if (event.target.matches('[data-answer]')) { event.preventDefault(); run(() => submitAnswer(event.target)); } });
document.addEventListener('change', event => {
  const target = event.target;
  if (target.matches('[data-weight]')) run(async () => {
    const row = state.batches.find(batch => batch.id === Number(target.dataset.id));
    try { await api(`batches/${row.id}`,'PATCH',{[target.dataset.weight]:target.value}); notice('权重已保存'); }
    finally { await loadBatches(); }
  });
});
document.addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button || button.disabled) return;
  if (button.dataset.view) return run(() => showView(button.dataset.view));
  if (button.dataset.next || button.dataset.start) return run(() => nextQuestion(button.dataset.next || button.dataset.start));
  const action = button.dataset.action, id = Number(button.dataset.id);
  if (!action) return;
  run(async () => {
    if (action === 'open-batch') { state.batchId = id; $('#word-search').value = ''; renderBatches(); await loadWords(); }
    if (action === 'edit-batch') batchEditor(state.batches.find(batch => batch.id === id));
    if (action === 'delete-batch') confirmAction('删除此批次及其中的单词？学习记录、已有错题和原始批次信息仍会保留。', async () => {
      await api(`batches/${id}`,'DELETE'); if (state.batchId === id) { state.batchId = null; $('#word-panel').hidden = true; } await loadBatches(); notice('批次已删除，历史与错题已保留');
    });
    if (action === 'edit-word') wordEditor(state.words.find(word => word.id === id));
    if (action === 'delete-word') confirmAction('从正常单词库删除此单词？已有错题和学习记录仍会保留。', async () => {
      await api(`words/${id}`,'DELETE'); await loadBatches(); await loadWords(); notice('单词已删除');
    });
    if (action === 'remove-mistake') confirmAction('将此词移出错题集？历史记录会保留，今后再答错时会重新进入错题集。', async () => {
      await api(`mistakes/${id}`,'DELETE'); await loadMistakes(); await loadBatches(); notice('已移出错题集');
    });
  });
});
run(async () => {
  try {
    const user = await api('me'); state.csrf = user.csrf; $('#username').textContent = user.username;
    $('#login-page').hidden = true; $('#app-page').hidden = false; await showView('study');
  } catch (error) { loggedOut(); if (error.message !== '请先登录') notice(error.message,true); }
});
