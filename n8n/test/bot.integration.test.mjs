// 整合測試：把 build 後的 Code node 程式放進模擬的 n8n 環境執行，
// mock Supabase(PostgREST)、TWSE、LINE reply API，驗證指令端到端行為。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHmac } from 'node:crypto';
import { createRequire } from 'node:module';

const here = dirname(fileURLToPath(import.meta.url));
const jsCode = [
  readFileSync(join(here, '../src/parser.js'), 'utf8'),
  readFileSync(join(here, '../src/line-stock-bot.main.js'), 'utf8'),
].join('\n');

const ENV = {
  SUPABASE_URL: 'https://fake.supabase.co',
  SUPABASE_SERVICE_ROLE_KEY: 'service-key',
  LINE_CHANNEL_SECRET: 'channel-secret',
  LINE_CHANNEL_ACCESS_TOKEN: 'access-token',
};

function makeFakeSupabase() {
  const db = {
    members: [],
    line_bindings: [],
    holdings: [],
    stocks: [
      { stock_no: '2330', name: '台積電' },
      { stock_no: '3231', name: '緯創' },
    ],
  };
  let nextId = 1;

  function handle(method, pathAndQuery, body) {
    const [table, queryStr] = pathAndQuery.split('?');
    const filters = [];
    for (const [key, value] of new URLSearchParams(queryStr || '')) {
      if (key === 'select' || key === 'on_conflict') continue;
      if (value.startsWith('eq.')) {
        filters.push((row) => String(row[key]) === value.slice(3));
      } else if (value.startsWith('in.(') && value.endsWith(')')) {
        const set = value.slice(4, -1).split(',');
        filters.push((row) => set.includes(String(row[key])));
      }
    }
    const match = (row) => filters.every((f) => f(row));

    if (method === 'GET') return db[table].filter(match);
    if (method === 'POST') {
      const incoming = Array.isArray(body) ? body : [body];
      return incoming.map((b) => {
        if (table === 'line_bindings') {
          const existing = db[table].find((r) => r.line_user_id === b.line_user_id);
          if (existing) return Object.assign(existing, b);
        }
        const row = { ...b };
        if (table === 'members' || table === 'holdings') row.id = nextId++;
        db[table].push(row);
        return row;
      });
    }
    if (method === 'PATCH') {
      const rows = db[table].filter(match);
      rows.forEach((r) => Object.assign(r, body));
      return rows;
    }
    if (method === 'DELETE') {
      const rows = db[table].filter(match);
      db[table] = db[table].filter((r) => !match(r));
      return rows;
    }
    throw new Error(`unexpected method ${method}`);
  }
  return { db, handle };
}

function makeRuntime({ twseResponse }) {
  const supabase = makeFakeSupabase();
  const replies = [];
  const helpers = {
    async httpRequest(opts) {
      const method = (opts.method || 'GET').toUpperCase();
      if (opts.url.startsWith('https://api.line.me/')) {
        replies.push(opts.body);
        return {};
      }
      if (opts.url.startsWith('https://www.twse.com.tw/')) {
        return twseResponse;
      }
      if (opts.url.startsWith(`${ENV.SUPABASE_URL}/rest/v1/`)) {
        return supabase.handle(method, opts.url.split('/rest/v1/')[1], opts.body);
      }
      throw new Error(`unexpected url ${opts.url}`);
    },
    async getBinaryDataBuffer() {
      return helpers._rawBody;
    },
  };

  async function send(text, { lineUserId = 'U-test', badSignature = false } = {}) {
    const payload = {
      events: [
        {
          type: 'message',
          replyToken: 'reply-token',
          source: { userId: lineUserId },
          message: { type: 'text', text },
        },
      ],
    };
    const rawBody = Buffer.from(JSON.stringify(payload), 'utf8');
    helpers._rawBody = rawBody;
    const signature = badSignature
      ? 'bogus'
      : createHmac('sha256', ENV.LINE_CHANNEL_SECRET).update(rawBody).digest('base64');
    const $input = { first: () => ({ json: { headers: { 'x-line-signature': signature } } }) };
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const fn = new AsyncFunction('require', '$env', '$input', jsCode);
    return fn.call({ helpers }, createRequire(import.meta.url), ENV, $input);
  }

  const lastReply = () => (replies.length ? replies[replies.length - 1].messages[0].text : null);
  return { supabase, replies, send, lastReply };
}

const TWSE_OK = {
  stat: 'OK',
  data: [['115/07/02', '1,000', '2,355,000', '2,350.00', '2,360.00', '2,340.00', '2,355.00', '+5.00', '100']],
};

test('簽章錯誤時拒絕處理且不回覆', async () => {
  const rt = makeRuntime({ twseResponse: TWSE_OK });
  const result = await rt.send('登入dada', { badSignature: true });
  assert.equal(result[0].json.ok, false);
  assert.equal(rt.replies.length, 0);
});

test('完整流程：登入 → 新增 → 查詢 → 切換 → 刪除', async () => {
  const rt = makeRuntime({ twseResponse: TWSE_OK });

  await rt.send('登入dada');
  assert.match(rt.lastReply(), /已登入「dada」/);
  assert.equal(rt.supabase.db.members[0].name, 'dada');
  assert.equal(rt.supabase.db.line_bindings[0].member_id, rt.supabase.db.members[0].id);

  await rt.send('新增2330 1000 850');
  assert.match(rt.lastReply(), /已為 dada 新增 台積電（2330）1000 股＠850/);

  await rt.send('新增緯創');
  assert.match(rt.lastReply(), /已為 dada 新增 緯創（3231）（觀察，未記股數）/);

  await rt.send('我的股票');
  const list = rt.lastReply();
  assert.match(list, /📊 dada 的持股/);
  assert.match(list, /2330 台積電　收盤 2,355（07\/02）/);
  assert.match(list, /1,000 股｜市值 2,355,000｜損益 \+1,505,000（\+177\.1%）/);
  assert.match(list, /3231 緯創/);
  assert.match(list, /觀察中（未記股數）/);

  await rt.send('登入媽媽', { lineUserId: 'U-mom' });
  await rt.send('切換媽媽');
  assert.match(rt.lastReply(), /已切換為「媽媽」/);
  await rt.send('新增2330 500 900');
  assert.match(rt.lastReply(), /已為 媽媽 新增/);

  await rt.send('切換dada');
  await rt.send('刪除2330');
  assert.match(rt.lastReply(), /已刪除 dada 的 台積電（2330），共 1 筆/);
  const dadaId = rt.supabase.db.members.find((m) => m.name === 'dada').id;
  assert.equal(rt.supabase.db.holdings.filter((h) => h.member_id === dadaId && h.stock_no === '2330').length, 0);
});

test('未登入時提示先登入；亂輸入回指令說明', async () => {
  const rt = makeRuntime({ twseResponse: TWSE_OK });
  await rt.send('我的股票');
  assert.match(rt.lastReply(), /請先輸入「登入你的名字」/);
  await rt.send('哈囉');
  assert.match(rt.lastReply(), /指令說明/);
});

test('TWSE 查無資料時顯示警告而不中斷', async () => {
  const rt = makeRuntime({ twseResponse: { stat: '很抱歉，沒有符合條件的資料!', total: 0 } });
  await rt.send('登入dada');
  await rt.send('新增2330');
  await rt.send('我的股票');
  assert.match(rt.lastReply(), /⚠️ 查無報價/);
});
