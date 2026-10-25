const LS_CONV = 'travel_agent_convs_v1';
const LS_USER = 'travel_agent_user';
const NODE_LABELS = {
  normalize_input: '归一化输入',
  classify_and_extract: '意图识别与槽位抽取',
  ask_clarify: '缺槽追问',
  chat: '闲聊回答',
  retrieve: '知识库检索',
  call_tools: '工具调用',
  validate_plan: '冲突校验',
  compose: '组织答案',
  persist: '写入轨迹'
};

const $ = (id) => document.getElementById(id);
const chatBox = $('chat');
const inner = $('inner');
const historyBox = $('history');
const input = $('msg');
const sendBtn = $('send');
const sidebar = $('sidebar');

let convs = loadConvs();
let currentId = null;
let busy = false;
let thinking = false;
let smartSearch = true;

const userId = (() => {
  let v = localStorage.getItem(LS_USER);
  if (!v) { v = 'u-' + uid().slice(0, 8); localStorage.setItem(LS_USER, v); }
  return v;
})();
$('userSub').textContent = userId;

function loadConvs(){
  try {
    const v = JSON.parse(localStorage.getItem(LS_CONV) || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}
function saveConvs(){
  try { localStorage.setItem(LS_CONV, JSON.stringify(convs)); } catch (e) {}
}
async function syncLocalMemory(){
  if (!convs.length) return;
  try {
    await fetch('/api/v1/memory/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        conversations: convs.map(c => ({
          id: c.id,
          threadId: c.threadId,
          updatedAt: c.updatedAt,
          messages: (c.messages || []).map(m => ({
            role: m.role, content: m.content, at: m.at,
            traceId: m.traceId, citations: m.citations || []
          }))
        }))
      })
    });
  } catch (e) {
    // 本地对话仍保存在 localStorage，记忆补录失败不影响聊天。
  }
}
function uid(){
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g,'').slice(0,16);
  return Math.random().toString(16).slice(2,14);
}
function currentConv(){ return convs.find(c => c.id === currentId) || null; }
function scrollBottom(){ chatBox.scrollTop = chatBox.scrollHeight; }
function nodeLabel(name){ return NODE_LABELS[name] || name || 'step'; }

function bucket(ts){
  const d = new Date(ts), n = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(n) - day(d)) / 86400000);
  if (diff <= 0) return '今天';
  if (diff === 1) return '昨天';
  if (diff < 7) return '7 天内';
  return '更早';
}

function renderHistory(){
  const q = ($('convSearch').value || '').trim().toLowerCase();
  historyBox.innerHTML = '';
  const list = convs
    .filter(c => !q || (c.title || '').toLowerCase().includes(q))
    .sort((a,b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  if (!list.length){
    const empty = document.createElement('div');
    empty.className = 'sb-empty';
    empty.textContent = q ? '没有匹配的对话' : '还没有对话，点上方开启新对话';
    historyBox.appendChild(empty);
    return;
  }
  let lastGroup = '';
  for (const conv of list){
    const g = bucket(conv.updatedAt || Date.now());
    if (g !== lastGroup){
      const head = document.createElement('div');
      head.className = 'hgroup';
      head.textContent = g;
      historyBox.appendChild(head);
      lastGroup = g;
    }
    const btn = document.createElement('button');
    btn.className = 'conv' + (conv.id === currentId ? ' active' : '');
    btn.textContent = conv.title || '新对话';
    btn.title = conv.title || '';
    btn.addEventListener('click', () => openConv(conv.id));
    historyBox.appendChild(btn);
  }
}

function welcome(){
  const box = document.createElement('div');
  box.className = 'welcome';
  box.innerHTML = '<h2>今天想去哪里？</h2>' +
    '<p>可以问景点、排行程、查天气、找美食；信息不足时我会先追问。</p>' +
    '<div class="chips">' +
      '<button class="chip">帮我规划3天西安行程</button>' +
      '<button class="chip">西安有哪些必去的景点</button>' +
      '<button class="chip">明天从西安到北京怎么走</button>' +
      '<button class="chip">西安天气怎么样</button>' +
    '</div>';
  box.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
      input.value = chip.textContent;
      input.focus();
      autoGrow();
      updateSendState();   // 关键：程序化赋值不会触发 input 事件，必须手动刷新按钮状态
    });
  });
  inner.appendChild(box);
}

function renderAnswer(bubble, text, citations){
  bubble.textContent = '';
  const parts = String(text || '').split(/(\[\d+\])/g);
  for (const part of parts){
    const m = part.match(/^\[(\d+)\]$/);
    if (m){
      const sup = document.createElement('sup');
      sup.className = 'cite';
      const a = document.createElement('a');
      a.href = '#cite-' + m[1];
      a.textContent = m[1];
      a.title = '查看来源 ' + m[1];
      a.addEventListener('click', () => {
        const box = bubble.closest('.acard');
        const src = box ? box.querySelector('.sources') : null;
        if (src) src.open = true;
      });
      sup.appendChild(a);
      bubble.appendChild(sup);
    } else if (part){
      bubble.appendChild(document.createTextNode(part));
    }
  }
}

function appendSources(card, citations){
  const seen = new Set();
  const list = [];
  for (const c of (citations || [])){
    if (!c || c.index == null || seen.has(c.index)) continue;
    seen.add(c.index);
    list.push(c);
  }
  if (!list.length) return;
  const det = document.createElement('details');
  det.className = 'sources';
  const sum = document.createElement('summary');
  sum.textContent = '来源 · ' + list.length + ' 条';
  const ol = document.createElement('ol');
  for (const c of list){
    const li = document.createElement('li');
    const anchor = document.createElement('a');
    anchor.id = 'cite-' + c.index;
    anchor.textContent = '[' + c.index + '] ';
    li.appendChild(anchor);
    const label = document.createElement('span');
    label.textContent = c.title || c.provider || '来源';
    li.appendChild(label);
    if (c.url){
      const link = document.createElement('a');
      link.href = c.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = '  打开';
      li.appendChild(link);
    }
    if (c.kind === 'tool'){
      const meta = document.createElement('span');
      meta.className = 'trace';
      meta.textContent = ' · ' + (c.provider || '') + (c.fetched_at ? ' · ' + c.fetched_at : '');
      li.appendChild(meta);
    }
    ol.appendChild(li);
  }
  det.appendChild(sum);
  det.appendChild(ol);
  card.appendChild(det);
}

function messageEl(msg){
  if (msg.role === 'user'){
    const row = document.createElement('div');
    row.className = 'row user';
    const who = document.createElement('div'); who.className = 'who'; who.textContent = '你';
    const bubble = document.createElement('div'); bubble.className = 'bubble';
    bubble.textContent = msg.content || '';
    row.appendChild(who); row.appendChild(bubble);
    return row;
  }
  const row = document.createElement('div');
  row.className = 'row assistant';
  const who = document.createElement('div'); who.className = 'who'; who.textContent = '助手';
  const card = document.createElement('div'); card.className = 'acard';
  const steps = document.createElement('div'); steps.className = 'steps';
  if ((msg.steps || []).length){
    const totalMs = (msg.steps || []).reduce((sum, s) => sum + (s.latency_ms || 0), 0);
    const det = document.createElement('details');
    det.className = 'process'; det.open = false;
    const sum = document.createElement('summary');
    sum.textContent = '执行过程 · ' + msg.steps.length + ' 步 · 总耗时 '
      + (totalMs / 1000).toFixed(1) + 's';
    det.appendChild(sum); det.appendChild(steps);
    for (const s of msg.steps){
      const line = document.createElement('div');
      line.className = 'step';
      line.textContent = (s.label || nodeLabel(s.node))
        + (s.latency_ms != null ? ' · ' + s.latency_ms + 'ms' : '');
      steps.appendChild(line);
    }
    card.appendChild(det);
  }
  const bubble = document.createElement('div'); bubble.className = 'bubble';
  renderAnswer(bubble, msg.content || '', msg.citations || []);
  card.appendChild(bubble);
  appendSources(card, msg.citations || []);
  if (msg.error){
    const err = document.createElement('div'); err.className = 'error';
    const strong = document.createElement('strong'); strong.textContent = '请求失败';
    const pre = document.createElement('pre'); pre.textContent = msg.error;
    err.appendChild(strong); err.appendChild(pre); card.appendChild(err);
  }
  if (msg.traceId){
    const det = document.createElement('details'); det.className = 'debug';
    const sum = document.createElement('summary'); sum.textContent = '调试信息';
    const tr = document.createElement('div'); tr.className = 'trace';
    tr.textContent = 'trace: ' + msg.traceId + (msg.threadId ? ' · thread: ' + msg.threadId : '');
    det.appendChild(sum); det.appendChild(tr); card.appendChild(det);
  }
  row.appendChild(who); row.appendChild(card);
  return row;
}

function renderChat(){
  inner.innerHTML = '';
  const conv = currentConv();
  if (!conv || !(conv.messages || []).length){ welcome(); return; }
  for (const msg of conv.messages) inner.appendChild(messageEl(msg));
  scrollBottom();
}

function openConv(id){
  currentId = id;
  renderChat();
  renderHistory();
  sidebar.classList.remove('open');
}

function newChat(){
  currentId = null;
  renderChat();
  renderHistory();
  input.value = '';
  autoGrow();
  updateSendState();
  input.focus();
  sidebar.classList.remove('open');
}

function appendUser(text){
  const row = document.createElement('div');
  row.className = 'row user';
  const who = document.createElement('div'); who.className = 'who'; who.textContent = '你';
  const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = text;
  row.appendChild(who); row.appendChild(bubble);
  inner.appendChild(row);
}

function appendAssistant(){
  const row = document.createElement('div');
  row.className = 'row assistant';
  const who = document.createElement('div'); who.className = 'who'; who.textContent = '助手';
  const card = document.createElement('div'); card.className = 'acard';
  const det = document.createElement('details'); det.className = 'process'; det.open = false;
  const sum = document.createElement('summary'); sum.textContent = '执行过程 · 0 步 · 总耗时 0.0s';
  const steps = document.createElement('div'); steps.className = 'steps';
  det.appendChild(sum); det.appendChild(steps); card.appendChild(det);
  const bubble = document.createElement('div'); bubble.className = 'bubble';
  card.appendChild(bubble);
  const err = document.createElement('div'); err.className = 'error'; err.style.display = 'none';
  card.appendChild(err);
  const debug = document.createElement('details'); debug.className = 'debug';
  const debugSum = document.createElement('summary'); debugSum.textContent = '调试信息';
  const trace = document.createElement('div'); trace.className = 'trace';
  debug.appendChild(debugSum); debug.appendChild(trace); card.appendChild(debug);
  row.appendChild(who); row.appendChild(card);
  inner.appendChild(row);
  scrollBottom();
  return { card, det, sum, steps, bubble, err, trace, totalMs: 0 };
}

function addStep(refs, node, ms, detail){
  const line = document.createElement('div');
  line.className = 'step';
  const t = ms != null ? ' · ' + ms + 'ms' : '';
  const d = detail ? ' — ' + detail : '';
  line.textContent = nodeLabel(node) + t + d;
  refs.steps.appendChild(line);
  refs.totalMs = (refs.totalMs || 0) + (ms || 0);
  refs.sum.textContent = '执行过程 · ' + refs.steps.children.length + ' 步 · 总耗时 '
    + (refs.totalMs / 1000).toFixed(1) + 's';
  scrollBottom();
}

function showError(refs, message){
  refs.err.style.display = 'block';
  refs.err.innerHTML = '';
  const strong = document.createElement('strong'); strong.textContent = '请求失败';
  const pre = document.createElement('pre'); pre.textContent = message;
  refs.err.appendChild(strong); refs.err.appendChild(pre);
}

function autoGrow(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 150) + 'px';
}

function updateSendState(){
  sendBtn.disabled = busy || !input.value.trim();
}

async function send(){
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true; updateSendState();

  if (!currentId){
    const id = uid();
    convs.unshift({ id, threadId: id, title: text.slice(0, 26), updatedAt: Date.now(), messages: [] });
    currentId = id;
    inner.innerHTML = '';
  }
  const conv = currentConv();
  if (!conv) return;

  conv.messages.push({ role: 'user', content: text, at: Date.now() });
  conv.updatedAt = Date.now();
  if (!conv.title || conv.title === '新对话') conv.title = text.slice(0, 26);
  saveConvs();
  appendUser(text);
  input.value = ''; autoGrow();

  const msg = { role: 'assistant', content: '', steps: [], citations: [],
                traceId: '', threadId: conv.threadId, error: '' };
  conv.messages.push(msg);
  const refs = appendAssistant();
  const caret = document.createElement('span');
  caret.className = 'typing';
  refs.bubble.appendChild(caret);

  try {
    const res = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({
        message: text,
        user_id: userId,
        thread_id: conv.threadId,
        stream: true,
        thinking: thinking,
        smart_search: smartSearch
      })
    });
    if (!res.ok || !res.body) throw new Error('请求失败：HTTP ' + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let finished = false;
    while (!finished){
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks){
        const line = chunk.split('\n').map(s => s.trim()).find(s => s.startsWith('data:'));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        let evt;
        try { evt = JSON.parse(raw); } catch (e) { continue; }

        if (evt.type === 'meta'){
          msg.traceId = evt.trace_id || '';
          refs.trace.textContent = 'trace: ' + msg.traceId + ' · thread: ' + (evt.thread_id || '');
        } else if (evt.type === 'step'){
          const label = nodeLabel(evt.node);
          msg.steps.push({ node: evt.node, label, latency_ms: evt.latency_ms, detail: evt.detail });
          addStep(refs, evt.node, evt.latency_ms, evt.detail);
        } else if (evt.type === 'token'){
          msg.content += evt.content || '';
          refs.bubble.textContent = msg.content;
          refs.bubble.appendChild(caret);
          scrollBottom();
        } else if (evt.type === 'final'){
          if (evt.answer) msg.content = evt.answer;
          msg.citations = evt.citations || [];
          renderAnswer(refs.bubble, msg.content, msg.citations);
          appendSources(refs.card, msg.citations);
          finished = true;
        } else if (evt.type === 'error'){
          msg.error = evt.message || '未知错误';
          refs.bubble.textContent = '';
          showError(refs, msg.error);
          finished = true;
        }
      }
    }
  } catch (err){
    msg.error = err.message || String(err);
    refs.bubble.textContent = '';
    showError(refs, msg.error);
  } finally {
    caret.remove();
    busy = false;
    conv.updatedAt = Date.now();
    saveConvs();
    renderHistory();
    updateSendState();
    input.focus();
    scrollBottom();
  }
}

$('newChat').addEventListener('click', newChat);
$('menuBtn').addEventListener('click', () => sidebar.classList.toggle('open'));
$('convSearch').addEventListener('input', renderHistory);
$('thinkBtn').addEventListener('click', (e) => {
  thinking = !thinking;
  e.currentTarget.classList.toggle('active', thinking);
  e.currentTarget.setAttribute('aria-pressed', String(thinking));
});
$('searchBtn').addEventListener('click', (e) => {
  smartSearch = !smartSearch;
  e.currentTarget.classList.toggle('active', smartSearch);
  e.currentTarget.setAttribute('aria-pressed', String(smartSearch));
});
input.addEventListener('input', () => { autoGrow(); updateSendState(); });
input.addEventListener('focus', updateSendState);
input.addEventListener('change', updateSendState);
input.addEventListener('compositionend', () => { autoGrow(); updateSendState(); });
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); }
});
sendBtn.addEventListener('click', send);

renderHistory();
renderChat();
updateSendState();
syncLocalMemory();
