#!/usr/bin/env node
/* Minimal Chrome DevTools Protocol helper for WorkBuddy.
 * Usage:
 *   node cdp.js list
 *   node cdp.js eval "<javascript>"
 *   node cdp.js eval-file <path>
 * Requires WorkBuddy started with --remote-debugging-port=<port>.
 */
'use strict';

const fs = require('fs');
const PORT = process.env.WB_CDP_PORT || '9223';
const BASE = `http://127.0.0.1:${PORT}`;

async function listTargets() {
  const res = await fetch(`${BASE}/json/list`);
  if (!res.ok) throw new Error(`GET /json/list -> ${res.status}`);
  return res.json();
}

function pickPage(targets) {
  const pages = targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  const wb = pages.filter((t) => /workbuddy|app\.asar|localhost/i.test(t.url || ''));
  return (wb.length ? wb : pages)[0];
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const pending = new Map();
    let id = 0;
    ws.addEventListener('open', () => resolve({
      send(method, params = {}) {
        const msgId = ++id;
        ws.send(JSON.stringify({ id: msgId, method, params }));
        return new Promise((res, rej) => {
          pending.set(msgId, { res, rej });
          setTimeout(() => {
            if (pending.has(msgId)) {
              pending.delete(msgId);
              rej(new Error(`${method} timeout`));
            }
          }, 20000);
        });
      },
      close() { try { ws.close(); } catch (_) {} },
    }));
    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(typeof ev.data === 'string' ? ev.data : ev.data.toString()); } catch (_) { return; }
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) rej(new Error(`${msg.error.message} (${msg.error.code})`));
        else res(msg.result);
      }
    });
    ws.addEventListener('error', () => reject(new Error('websocket error')));
    ws.addEventListener('close', () => reject(new Error('websocket closed')));
  });
}

async function main() {
  const [cmd, arg] = process.argv.slice(2);
  if (!cmd) {
    console.error('usage: cdp.js list | eval <js> | eval-file <path>');
    process.exit(2);
  }
  const targets = await listTargets();
  if (cmd === 'list') {
    for (const t of targets) {
      console.log(`${t.type}\t${t.title || ''}\t${t.url || ''}\t${t.webSocketDebuggerUrl ? 'ws' : '-'}`);
    }
    return;
  }
  let expression = arg || '';
  if (cmd === 'eval-file') expression = fs.readFileSync(arg, 'utf8');
  if (cmd !== 'eval' && cmd !== 'eval-file') {
    console.error(`unknown command: ${cmd}`);
    process.exit(2);
  }
  const target = pickPage(targets);
  if (!target) throw new Error('no page target');
  const client = await connect(target.webSocketDebuggerUrl);
  try {
    await client.send('Runtime.enable');
    const result = await client.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
      allowUnsafeEvalBlockedByCSP: true,
    });
    if (result.exceptionDetails) {
      console.error('EXCEPTION:', JSON.stringify(result.exceptionDetails, null, 2));
      process.exitCode = 1;
    } else {
      const v = result.result && result.result.value;
      console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 2));
    }
  } finally {
    client.close();
  }
}

main().catch((err) => {
  console.error('ERROR:', err.message);
  process.exit(1);
});
