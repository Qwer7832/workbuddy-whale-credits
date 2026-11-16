#!/usr/bin/env node
/* Keeps WorkBuddy's home-page slogan replaced with the credits line.
 *
 * WorkBuddy renders that line with CSS:
 *   .wb-home-route:not(.wb-calendar-route)::after{content:var(--wb-home-slogan,"内容由 AI 生成…")}
 * Both the stylesheet and the session preload are fixed at process start, so the only
 * reliable way in is the DevTools protocol: inject at document-start for every future
 * document, plus evaluate right away for whatever is already loaded.
 *
 * Started by css_daemon.py with CREATE_NO_WINDOW; no visible window of its own.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const PORT = process.env.WB_CDP_PORT || '9223';
const BASE = `http://127.0.0.1:${PORT}`;
const SKILL_DIR = path.join(process.env.USERPROFILE || process.env.HOME || '', '.workbuddy', 'skills', 'wb-whale-credits');
const CONFIG = path.join(SKILL_DIR, 'config', 'credits.json');
const LOG = path.join(SKILL_DIR, 'config', 'inject.log');
const STYLE_ID = 'wb-credits-style';

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  try { fs.appendFileSync(LOG, line); } catch (_) {}
  if (process.env.WB_CDP_VERBOSE) process.stdout.write(line);
}

function trimLog() {
  try {
    const st = fs.statSync(LOG);
    if (st.size > 200 * 1024) fs.writeFileSync(LOG, '');
  } catch (_) {}
}

function plain(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '0';
  return Math.abs(v % 1) < 1e-9 ? v.toFixed(0) : v.toFixed(2);
}

function sloganText() {
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG, 'utf8'));
    /* 小数分隔符：点号（与 css_daemon.py 的 TEXT_DECIMAL_SEP 一致）；想回逗号就改这里的 '.' */
    const num = (v) => plain(v);
    return `积分：${num(cfg.balance)} 今日已用：${num(cfg.todayUsage || 0)}`;
  } catch (_) {
    return null;
  }
}

function usageMap() {
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG, 'utf8'));
    const raw = (cfg.ledger && cfg.ledger.bySession) || {};
    const out = {};
    for (const k of Object.keys(raw)) out[k] = Math.round(Number(raw[k]) * 100) / 100;
    return out;
  } catch (_) {
    return {};
  }
}

function sourceFor(text, usage) {
  const lit = JSON.stringify(text);          // "积分：2082.55 今日已用：171.05"
  const usageLit = JSON.stringify(usage || {});
  const styleId = JSON.stringify(STYLE_ID);
  return `(function(){
  var T=${lit};
  var U=${usageLit};
  var DIS='内容由 AI 生成，请核实重要信息';
  var SEP='·';
  var W=window.__wbCredits;

  /* 小数分隔符用点号（中文习惯写法，如 2103.93），与 css_daemon.py 的 TEXT_DECIMAL_SEP 保持一致 */
  function num(v){
    var n=Number(v);
    if(!isFinite(n)) return '0';
    return Math.abs(n%1)<1e-9 ? n.toFixed(0) : n.toFixed(2);
  }
  function escCss(s){
    var o='';
    s=String(s);
    for(var i=0;i<s.length;i++){
      var c=s.charAt(i);
      if(c==='"' || c==='\\\\') o+='\\\\';
      o+=c;
    }
    return o;
  }
  /* 当前会话 id：WorkBuddy 的选中态是 CSS Modules 哈希类名（_selected_1ufy3_20），
     还可能是 --active / is-active / aria-current，全部一并认。 */
  function activeConv(){
    var re=/--active|is-active|_active_|selected/i;
    var els=document.querySelectorAll('[data-conversation-id]');
    for(var i=0;i<els.length;i++){
      var e=els[i], p=e;
      for(var k=0;k<5&&p;k++){
        if(re.test(String(p.className||''))) return e.getAttribute('data-conversation-id');
        p=p.parentElement;
      }
    }
    var sel=document.querySelector('[aria-current="page"],[aria-current="true"]');
    if(sel){
      var box=sel;
      for(var k2=0;k2<5&&box;k2++){
        var id=box.getAttribute && box.getAttribute('data-conversation-id');
        if(id) return id;
        box=box.parentElement;
      }
      var inner=sel.querySelector && sel.querySelector('[data-conversation-id]');
      if(inner) return inner.getAttribute('data-conversation-id');
    }
    return null;
  }
  /* 只在**完全认不出**选中项时兜底（首页/列表收起），且要求账本仅一个会话才敢用 */
  function latestUsage(){
    var ks=W.usage?Object.keys(W.usage):[];
    if(ks.length!==1) return null;
    var v=Number(W.usage[ks[0]]);
    return isFinite(v)?v:null;
  }
  function label(){
    var t=W.text;
    var cid=activeConv();
    var used;
    if(cid){
      /* 认出了会话就以它为准：有记录用记录，没记录就是 0。
         绝不能回退到别的会话的数字——那会让所有会话显示成同一个值（串台）。 */
      used=(W.usage && W.usage[cid]!==undefined && W.usage[cid]!==null) ? W.usage[cid] : 0;
    }else{
      used=latestUsage();
    }
    if(used===undefined || used===null) return t;
    return t+' 本会话已用：'+num(used);
  }

  function go(){
    try{
      var r=document.documentElement; if(!r) return;
      var line=label();
      r.style.setProperty('--wb-home-slogan', "'"+line+"'");

      /* 会话底部：把「积分 今日 本会话」插到免责声明左边，保留原文 */
      var boxes=document.querySelectorAll('.wb-homepage-disclaimer');
      for(var i=0;i<boxes.length;i++){
        var box=boxes[i], anchor=null, kids=box.children;
        for(var j=0;j<kids.length;j++){
          var el=kids[j];
          if(el.tagName==='SPAN' && el.textContent && el.textContent.indexOf('内容由 AI 生成')>=0){ anchor=el; break; }
        }
        if(!anchor) continue;
        var mine=box.querySelector('[data-wb-credits="slogan"]');
        if(!mine){
          mine=document.createElement('span');
          mine.setAttribute('data-wb-credits','slogan');
          box.insertBefore(mine, anchor);
        }
        if(mine.textContent!==line) mine.textContent=line;
        /* 只留积分那一行：拆掉分隔符，隐藏官方声明 */
        var seps=box.querySelectorAll('[data-wb-credits="sep"]');
        for(var q=0;q<seps.length;q++) seps[q].parentNode.removeChild(seps[q]);
        for(var j2=0;j2<kids.length;j2++){
          var el2=kids[j2];
          if(el2===mine || el2.getAttribute('data-wb-credits')) continue;
          if(el2.textContent && el2.textContent.indexOf('内容由 AI 生成')>=0)
            el2.setAttribute('data-wb-credits','hidden');
        }
      }

      /* 首页伪元素：有可见的 DOM 声明就清空它，否则自己拼一行 */
      var visible=false;
      for(var k2=0;k2<boxes.length;k2++){
        var b=boxes[k2];
        if(b.offsetParent!==null && b.getBoundingClientRect().height>0){ visible=true; break; }
      }
      var s=document.getElementById(${styleId});
      if(!s){ s=document.createElement('style'); s.id=${styleId}; (document.head||r).appendChild(s); }
      var bold='.wb-homepage-disclaimer,.wb-homepage-disclaimer span{font-weight:600 !important;}'
             + '[data-wb-credits="hidden"]{display:none !important;}';
      /* 比继承来的 rgba(0,0,0,.4) 明显更深；跟随主题变量，深色主题下自动变亮 */
      var deep='.wb-homepage-disclaimer [data-wb-credits="slogan"]'
             + '{color:var(--wb-credits-text-color,var(--wb-color-text-primary,#111)) !important;}';
      var css=visible
        ? bold+deep+'.wb-home-route:not(.wb-calendar-route)::after{content:"" !important;}'
        : bold+deep+'.wb-home-route:not(.wb-calendar-route)::after{content:"'+escCss(line)+'" !important;font-weight:600 !important;color:var(--wb-credits-text-color,var(--wb-color-text-primary,#111)) !important;}';
      if(s.textContent!==css) s.textContent=css;
    }catch(e){}
  }

  if(!W){
    W=window.__wbCredits={text:T,usage:U,go:go,queued:false};
    W.again=function(){
      if(W.queued) return;
      W.queued=true;
      setTimeout(function(){ W.queued=false; try{ W.go(); }catch(e){} }, 80);
    };
    try{ new MutationObserver(W.again).observe(document.documentElement,
          {childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['class']}); }catch(e){}
    try{ setInterval(W.again, 1500); }catch(e){}
  }
  W.text=T; W.usage=U; W.go=go;   /* 热更新时必须换掉句柄，否则旧闭包一直生效 */
  W.go();
})();`;
}

async function listTargets() {
  const res = await fetch(`${BASE}/json/list`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let seq = 0;
    const pending = new Map();
    const client = {
      open: false,
      onclose: null,
      send(method, params = {}) {
        const id = ++seq;
        ws.send(JSON.stringify({ id, method, params }));
        return new Promise((res, rej) => {
          pending.set(id, { res, rej });
          setTimeout(() => {
            if (pending.has(id)) { pending.delete(id); rej(new Error(`${method} timeout`)); }
          }, 15000);
        });
      },
      close() { try { ws.close(); } catch (_) {} },
    };
    ws.addEventListener('open', () => { client.open = true; resolve(client); });
    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(typeof ev.data === 'string' ? ev.data : ev.data.toString()); } catch (_) { return; }
      if (msg.id && pending.has(msg.id)) {
        const p = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) p.rej(new Error(`${msg.error.message}`));
        else p.res(msg.result);
      } else if (msg.method === 'Inspector.detached') {
        client.close();
      }
    });
    ws.addEventListener('close', () => { client.open = false; if (client.onclose) client.onclose(); });
    ws.addEventListener('error', () => { if (!client.open) reject(new Error('ws error')); });
  });
}

const attached = new Map();   // targetId -> client
let lastText = null;
let lastUsage = {};
let lastKey = '';

function currentSource() {
  const text = sloganText() || lastText || '';
  return sourceFor(text, lastUsage);
}

async function attach(target) {
  const client = await connect(target.webSocketDebuggerUrl);
  await client.send('Runtime.enable');
  await client.send('Page.enable');
  const source = currentSource();
  try {
    await client.send('Page.addScriptToEvaluateOnNewDocument', { source });
  } catch (_) {}
  await client.send('Runtime.evaluate', {
    expression: source, returnByValue: true, allowUnsafeEvalBlockedByCSP: true,
  });
  client.onclose = () => {
    attached.delete(target.id);
    log(`detached ${target.id} ${target.url || ''}`);
  };
  attached.set(target.id, client);
  log(`attached ${target.id} ${(target.url || '').slice(0, 80)}`);
}

async function evaluateAll() {
  const src = currentSource();
  for (const [tid, client] of attached) {
    if (!client.open) { attached.delete(tid); continue; }
    try {
      await client.send('Runtime.evaluate', {
        expression: src, returnByValue: true, allowUnsafeEvalBlockedByCSP: true,
      });
    } catch (err) {
      log(`eval failed ${tid}: ${err.message}`);
      attached.delete(tid);
    }
  }
}

async function tick() {
  const text = sloganText();
  const usage = usageMap();
  const key = JSON.stringify([text, usage]);
  if (text && key !== lastKey) {
    const changed = lastKey !== '';
    lastText = text;
    lastUsage = usage;
    lastKey = key;
    if (changed) {
      log(`text -> ${text} (sessions: ${Object.keys(usage).length})`);
      await evaluateAll();
    }
  }
  let targets = [];
  try { targets = await listTargets(); } catch (_) { return; }   // port closed: app not started yet
  for (const t of targets) {
    if (t.type !== 'page' || !t.webSocketDebuggerUrl) continue;
    if (attached.has(t.id)) continue;
    try { await attach(t); } catch (err) { log(`attach failed ${t.id}: ${err.message}`); }
  }
}

async function main() {
  trimLog();
  log(`injector start pid=${process.pid} port=${PORT}`);
  for (;;) {
    try { await tick(); } catch (err) { log(`tick error: ${err.message}`); }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

main();
