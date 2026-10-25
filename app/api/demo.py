# -*- coding: utf-8 -*-
"""双页面 UI：导航首页 / 与 DeepSeek 式对话调试台 /demo。

背景图：/static/coast.jpg（用户提供），低透明度 + 渐变蒙版保证文字可读。
样式：Tailwind Play CDN + 本地兜底 CSS（离线时布局仍然可用）。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

SHARED_CSS = """
:root{
  --ink:#16333d; --ink-2:#2b4a55; --muted:#6b818c;
  --blue:#2f7de1; --blue-dark:#2569c4; --soft:#e9f4ff;
  --line:rgba(255,255,255,.68);
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--ink); background:#e6f6fd; -webkit-font-smoothing:antialiased;
}
.bg{
  position:fixed; inset:-2%; z-index:-3;
  background:url('/static/coast.jpg') center/cover no-repeat;
  filter:blur(1.1px) saturate(1.06); transform:scale(1.01);
}
.veil{
  position:fixed; inset:0; z-index:-2; pointer-events:none;
  background:linear-gradient(180deg,rgba(255,255,255,.56),rgba(232,247,255,.70) 55%,rgba(255,255,255,.84));
}
.veil.strong{background:linear-gradient(180deg,rgba(255,255,255,.74),rgba(232,247,255,.80))}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.code{background:rgba(236,244,248,.92);padding:3px 8px;border-radius:6px;
      font-family:ui-monospace,Consolas,monospace;font-size:12px}

/* ---------- 首页 ---------- */
.landing{min-height:100%;display:flex;align-items:center;justify-content:center;padding:34px 18px}
.hero{
  width:min(760px,100%);padding:42px 42px 28px;border-radius:28px;
  background:rgba(255,255,255,.80);border:1px solid var(--line);
  backdrop-filter:blur(20px) saturate(1.15);
  box-shadow:0 24px 70px rgba(24,80,110,.16);
}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:#2b6cb0;
       background:#e8f3ff;border:1px solid #cfe6ff;border-radius:999px;padding:4px 10px}
.hero h1{font-size:clamp(24px,3.2vw,32px);margin:14px 0 8px;letter-spacing:.2px}
.hero .sub{color:var(--muted);margin:0 0 26px;font-size:14px}
.nav{display:flex;flex-direction:column;gap:10px;margin:0 0 24px}
.nav-item{display:flex;align-items:center;gap:14px;padding:13px 16px;border-radius:16px;
          background:rgba(255,255,255,.66);border:1px solid rgba(255,255,255,.92);
          color:var(--ink);transition:.18s}
.nav-item:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(30,90,120,.12);
                text-decoration:none;background:#fff}
.nav-icon{width:38px;height:38px;border-radius:12px;flex:0 0 auto;display:grid;place-items:center;
          background:linear-gradient(135deg,#dff1ff,#eef8ff);color:var(--blue)}
.nav-text b{display:block;font-size:14px;font-weight:600}
.nav-text span{display:block;font-size:12px;color:var(--muted);margin-top:2px}
.nav-arrow{margin-left:auto;color:#9db4c0;font-size:16px}
.api{border-top:1px solid rgba(22,51,61,.08);padding-top:18px;display:flex;flex-direction:column;gap:9px}
.api-row{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--ink-2);flex-wrap:wrap}
.method{font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:3px 7px;border-radius:5px;color:#fff}
.method.post{background:#2f7de1}.method.get{background:#2fa36b}
.tag{font-size:10.5px;color:var(--blue);background:#e6f1ff;padding:2px 7px;border-radius:999px}
.foot{font-size:11.5px;color:#8698a2;line-height:1.85;margin-top:20px}

/* ---------- 调试台 ---------- */
.demo-body{overflow:hidden}
.app{height:100%;display:grid;grid-template-columns:272px minmax(0,1fr)}
.sidebar{
  background:rgba(255,255,255,.84);backdrop-filter:blur(22px) saturate(1.1);
  border-right:1px solid rgba(255,255,255,.8);display:flex;flex-direction:column;min-height:0;
}
.sb-head{padding:18px 16px 8px}
.brand{display:flex;align-items:center;gap:9px;font-weight:650;font-size:14px}
.logo{width:26px;height:26px;border-radius:9px;display:grid;place-items:center;color:#fff;
      background:linear-gradient(135deg,#2f7de1,#6ec3ff);font-size:14px}
.new-chat{margin-top:14px;width:100%;display:flex;align-items:center;gap:9px;padding:10px 12px;
          border-radius:12px;border:1px solid rgba(22,51,61,.10);background:#fff;color:var(--ink);
          font-size:13px;cursor:pointer;box-shadow:0 2px 6px rgba(20,60,80,.04)}
.new-chat:hover{border-color:#b9d8ff;color:var(--blue)}
.sb-search{padding:8px 16px 6px}
.sb-search input{width:100%;padding:8px 11px;border-radius:10px;border:1px solid rgba(22,51,61,.08);
                 background:rgba(255,255,255,.72);font-size:12px;outline:none}
.sb-search input:focus{border-color:#bcdcff;background:#fff}
.history{flex:1;overflow-y:auto;padding:4px 10px 12px}
.hgroup{font-size:11px;color:#93a6b0;padding:12px 8px 6px}
.conv{width:100%;text-align:left;padding:9px 10px;border-radius:10px;border:0;background:transparent;
      color:#33505c;font-size:12.5px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conv:hover{background:rgba(47,125,225,.07)}
.conv.active{background:rgba(47,125,225,.12);color:#1b5fae;font-weight:600}
.sb-empty{font-size:11.5px;color:#a0b0b8;padding:16px 10px}
.sb-foot{border-top:1px solid rgba(22,51,61,.07);padding:12px 14px;display:flex;align-items:center;gap:10px}
.avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:#fff;
        background:linear-gradient(135deg,#9ad4ff,#5aa9f7);font-size:14px;font-weight:600}
.user-name{font-size:12.5px;font-weight:600}
.user-sub{font-size:10.5px;color:#93a6b0}
.main{display:flex;flex-direction:column;min-width:0;min-height:0}
.topbar{padding:13px 26px 11px;border-bottom:1px solid rgba(255,255,255,.7);
        background:rgba(255,255,255,.55);backdrop-filter:blur(14px);
        display:flex;align-items:flex-start;gap:12px}
.topbar h1{font-size:15.5px;margin:0;font-weight:650}
.topbar p{font-size:11.5px;color:var(--muted);margin:3px 0 0}
.top-links{margin-left:auto;display:flex;gap:14px;font-size:12px;align-items:center;flex-wrap:wrap}
.prefs{display:none;font-size:11px;background:#eef4ff;color:#3b5d9a;border-radius:999px;padding:4px 9px}
.menu-btn{display:none;width:32px;height:32px;border-radius:9px;border:1px solid rgba(22,51,61,.10);
          background:#fff;cursor:pointer;place-items:center;color:#41606e}
.chat{flex:1;overflow-y:auto;padding:22px 20px 8px}
.chat-inner{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.welcome{margin:6vh auto 0;max-width:580px;text-align:center;color:var(--muted)}
.welcome h2{font-size:19px;color:#2b4a55;margin:0 0 8px;font-weight:600}
.welcome p{font-size:13px;margin:0 0 18px}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center}
.chip{border:1px solid rgba(22,51,61,.10);background:rgba(255,255,255,.86);border-radius:999px;
      padding:7px 13px;font-size:12px;color:#4b6470;cursor:pointer}
.chip:hover{border-color:#bcdcff;color:var(--blue)}
.row{display:flex;flex-direction:column}
.row.user{align-items:flex-end}
.row.assistant{align-items:stretch}
.who{font-size:10.5px;color:#93a6b0;margin:0 4px 5px}
.bubble{padding:11px 14px;border-radius:15px;font-size:14px;line-height:1.75;
        white-space:pre-wrap;word-break:break-word}
.row.user .bubble{background:#dceeff;border:1px solid #cfe6ff;border-bottom-right-radius:5px;
                  max-width:min(78%,640px)}
.acard{background:rgba(255,255,255,.93);border:1px solid rgba(255,255,255,.95);border-radius:15px;
       border-top-left-radius:5px;padding:12px 15px;box-shadow:0 6px 20px rgba(24,80,110,.06)}
.process{margin:0 0 9px;font-size:11.5px;color:#7b8f99}
.process summary{cursor:pointer;color:#5b7f92}
sup.cite{font-size:10px;line-height:0;margin-left:1px;vertical-align:super}
sup.cite a{color:#5b8fd6;text-decoration:none}
sup.cite a:hover{text-decoration:underline}
.sources{margin-top:9px;font-size:11.5px;color:#7b8f99}
.sources summary{cursor:pointer;color:#5b7f92}
.sources ol{margin:6px 0 0 18px;padding:0}
.sources li{margin:2px 0;line-height:1.6;word-break:break-word}
.sources a{color:#2f7de1;text-decoration:none}
.sources a:hover{text-decoration:underline}
.debug{margin-top:6px;font-size:10.5px;color:#a6b5bd}
.debug summary{cursor:pointer;color:#a6b5bd}
.steps{margin:7px 0 0;padding-left:11px;border-left:2px solid #e6eef2;
       display:flex;flex-direction:column;gap:3px}
.step{font-size:11.5px;color:#7b8f99;line-height:1.55}
.error{background:#fff8e1;border:1px solid #f3d48a;color:#7c5300;border-radius:9px;
       padding:9px 11px;margin-top:8px;font-size:12px}
.error strong{display:block;color:#b42318;margin-bottom:3px}
.error pre{margin:0;white-space:pre-wrap;word-break:break-all;
           font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.trace{margin-top:7px;font-size:10.5px;color:#a6b5bd;word-break:break-all}
.typing{display:inline-block;width:7px;height:14px;background:#b9c6cd;border-radius:2px;
        vertical-align:-2px;animation:blink 1s steps(2) infinite}
@keyframes blink{50%{opacity:0}}
.composer{padding:10px 20px 14px;
          background:linear-gradient(180deg,rgba(255,255,255,0),rgba(255,255,255,.62) 35%)}
.composer-inner{max-width:860px;margin:0 auto;background:rgba(255,255,255,.93);
                border:1px solid rgba(255,255,255,.96);border-radius:18px;
                box-shadow:0 10px 30px rgba(24,80,110,.09);padding:9px 10px 8px 12px}
.tool-row{display:flex;gap:8px;margin-bottom:6px}
.tool{font-size:11.5px;padding:5px 10px;border-radius:999px;border:1px solid rgba(22,51,61,.10);
      background:rgba(255,255,255,.8);color:#5b7480;cursor:pointer;display:flex;align-items:center;gap:5px}
.tool.active{background:#e8f3ff;border-color:#bcdcff;color:#1f66b8}
.input-row{display:flex;align-items:flex-end;gap:10px}
.input-row textarea{flex:1;border:0;outline:none;resize:none;background:transparent;font:inherit;
                    font-size:14px;line-height:1.65;min-height:26px;max-height:150px;padding:5px 0}
.send{width:34px;height:34px;border-radius:50%;border:0;background:var(--blue);color:#fff;
      font-size:17px;cursor:pointer;display:grid;place-items:center}
.send:hover{background:var(--blue-dark)}
.send:disabled{opacity:.4;cursor:not-allowed}
.hint{max-width:860px;margin:7px auto 0;text-align:center;font-size:10.5px;color:#93a6b0}
@media (max-width:900px){
  .app{grid-template-columns:1fr}
  .sidebar{position:fixed;z-index:20;top:0;bottom:0;left:0;width:282px;
           transform:translateX(-102%);transition:transform .22s ease;
           box-shadow:0 20px 50px rgba(0,0,0,.14)}
  .sidebar.open{transform:none}
  .menu-btn{display:grid}
  .topbar{padding:11px 14px}
  .top-links{gap:10px}
  .chat{padding:16px 12px 6px}
  .composer{padding:8px 12px 12px}
}
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>旅游规划助手 · Agent API</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>""" + SHARED_CSS + """</style>
</head>
<body>
<div class="bg"></div><div class="veil"></div>
<main class="landing">
  <section class="hero">
    <span class="badge">✦ PRD v1.2 · FastAPI</span>
    <h1>旅游规划助手 · Agent API</h1>
    <p class="sub">LangGraph 状态图 + RAG + 工具调用（PRD v1.2）</p>

    <nav class="nav">
      <a class="nav-item" href="/demo">
        <span class="nav-icon">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/><path d="M8 9h8M8 13h5"/>
          </svg>
        </span>
        <span class="nav-text"><b>打开调试台</b><span>实时看 step 与 token</span></span>
        <span class="nav-arrow">›</span>
      </a>
      <a class="nav-item" href="/docs">
        <span class="nav-icon">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
          </svg>
        </span>
        <span class="nav-text"><b>Swagger 文档</b><span>直接调用接口</span></span>
        <span class="nav-arrow">›</span>
      </a>
      <a class="nav-item" href="/health">
        <span class="nav-icon">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12h4l2-6 4 12 2-6h6"/>
          </svg>
        </span>
        <span class="nav-text"><b>健康检查</b><span>查看依赖与配置状态</span></span>
        <span class="nav-arrow">›</span>
      </a>
    </nav>

    <div class="api">
      <div class="api-row"><span class="method post">POST</span><code class="code">/api/v1/chat</code><span class="tag">SSE</span></div>
      <div class="api-row"><span class="method get">GET</span><code class="code">/api/v1/traces/{trace_id}</code></div>
    </div>

    <p class="foot">FastAPI 服务，地址 <b>127.0.0.1:8001</b>；<b>/</b> 是导航首页，<b>/demo</b> 是对话调试台，<b>/docs</b> Swagger，<b>/health</b> 健康检查。</p>
  </section>
</main>
</body>
</html>
"""

DEMO_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>旅游对话助手</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>""" + SHARED_CSS + """</style>
</head>
<body class="demo-body">
<div class="bg"></div><div class="veil strong"></div>

<div class="app">
  <aside class="sidebar" id="sidebar">
    <div class="sb-head">
      <div class="brand"><span class="logo">✈</span>旅游规划助手</div>
      <button class="new-chat" id="newChat">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14"/>
        </svg>
        开启新对话
      </button>
    </div>
    <div class="sb-search">
      <input id="convSearch" placeholder="搜索对话" autocomplete="off">
    </div>
    <div class="history" id="history"></div>
    <div class="sb-foot">
      <span class="avatar">旅</span>
      <div>
        <div class="user-name">旅行者</div>
        <div class="user-sub" id="userSub">本地会话</div>
      </div>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <button class="menu-btn" id="menuBtn" aria-label="打开会话列表">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>
      <div>
        <h1>旅游对话助手</h1>
        <p>告诉我你想去哪里，我可以帮你规划行程、查询天气、交通、住宿和美食。</p>
      </div>
      <div class="top-links">
        <a href="/health">健康检查</a>
      </div>
    </header>

    <div class="chat" id="chat"><div class="chat-inner" id="inner"></div></div>

    <div class="composer">
      <div class="composer-inner">
        <div class="tool-row">
          <button class="tool" id="thinkBtn" aria-pressed="false">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>
            </svg>
            深度思考
          </button>
          <button class="tool active" id="searchBtn" aria-pressed="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>
            </svg>
            智能搜索
          </button>
        </div>
        <div class="input-row">
          <textarea id="msg" rows="1" placeholder="给助手发送消息"></textarea>
          <button class="send" id="send" aria-label="发送" disabled>↑</button>
        </div>
      </div>
      <p class="hint">SSE 流式输出 · 左侧会话保存在本地 · 同一会话保持上下文</p>
    </div>
  </main>
</div>

<script>
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
  const parts = String(text || '').split(/(\\[\\d+\\])/g);
  for (const part of parts){
    const m = part.match(/^\\[(\\d+)\\]$/);
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
      const chunks = buffer.split('\\n\\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks){
        const line = chunk.split('\\n').map(s => s.trim()).find(s => s.startsWith('data:'));
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
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@router.get("/demo", response_class=HTMLResponse)
def demo():
    return DEMO_HTML
