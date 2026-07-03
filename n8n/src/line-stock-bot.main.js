// n8n Code node 主流程：驗 LINE 簽章 → 解析指令 → 讀寫 Supabase → 抓 TWSE 報價 → 回覆 LINE。
// 由 build.mjs 與 parser.js 併成單一 jsCode，於 n8n 內執行（runOnceForAllItems）。
// 依賴環境變數：SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY、LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN
// 依賴 n8n 環境變數 NODE_FUNCTION_ALLOW_BUILTIN 包含 crypto

const crypto = require('crypto');
const helpers = this.helpers;

const ENV = {
  SUPABASE_URL: $env.SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY: $env.SUPABASE_SERVICE_ROLE_KEY,
  LINE_CHANNEL_SECRET: $env.LINE_CHANNEL_SECRET,
  LINE_CHANNEL_ACCESS_TOKEN: $env.LINE_CHANNEL_ACCESS_TOKEN,
};
for (const [key, value] of Object.entries(ENV)) {
  if (!value) {
    throw new Error(`缺少環境變數 ${key}，請到 HF Space Settings 設定`);
  }
}

// ---------- Supabase (PostgREST) ----------
async function sb(method, pathAndQuery, body, prefer) {
  return helpers.httpRequest({
    method,
    url: `${ENV.SUPABASE_URL}/rest/v1/${pathAndQuery}`,
    headers: {
      apikey: ENV.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${ENV.SUPABASE_SERVICE_ROLE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: prefer || 'return=representation',
    },
    body,
    json: true,
  });
}

async function getMemberByName(name) {
  const rows = await sb('GET', `members?name=eq.${encodeURIComponent(name)}&select=id,name`);
  return rows[0] || null;
}

async function getActingMember(lineUserId) {
  const bindings = await sb(
    'GET',
    `line_bindings?line_user_id=eq.${encodeURIComponent(lineUserId)}&select=member_id,acting_member_id`
  );
  const binding = bindings[0];
  if (!binding) {
    return null;
  }
  const memberId = binding.acting_member_id || binding.member_id;
  if (!memberId) {
    return null;
  }
  const members = await sb('GET', `members?id=eq.${memberId}&select=id,name`);
  return members[0] || null;
}

// ---------- 指令處理 ----------
async function handleLogin(lineUserId, name) {
  let member = await getMemberByName(name);
  if (!member) {
    const created = await sb('POST', 'members', { name });
    member = created[0];
  }
  await sb(
    'POST',
    'line_bindings?on_conflict=line_user_id',
    {
      line_user_id: lineUserId,
      member_id: member.id,
      acting_member_id: member.id,
      updated_at: new Date().toISOString(),
    },
    'return=representation,resolution=merge-duplicates'
  );
  return `✅ 已登入「${member.name}」，之後的操作都會記在這個身份。`;
}

async function handleSwitch(lineUserId, name) {
  const member = await getMemberByName(name);
  if (!member) {
    return `❌ 找不到成員「${name}」，請先輸入「登入${name}」建立身份。`;
  }
  const bindings = await sb('GET', `line_bindings?line_user_id=eq.${encodeURIComponent(lineUserId)}&select=line_user_id`);
  if (!bindings[0]) {
    return '❌ 請先輸入「登入你的名字」完成綁定，再切換身份。';
  }
  await sb('PATCH', `line_bindings?line_user_id=eq.${encodeURIComponent(lineUserId)}`, {
    acting_member_id: member.id,
    updated_at: new Date().toISOString(),
  });
  return `🔁 已切換為「${member.name}」，之後的新增/查詢都作用在這個帳戶。`;
}

async function resolveStock(input) {
  if (/^\d{4,6}[A-Z]?$/.test(input)) {
    const rows = await sb('GET', `stocks?stock_no=eq.${encodeURIComponent(input)}&select=stock_no,name`);
    // 代號查不到對照表仍允許新增（可能是上櫃股），名稱先以代號代替
    return rows[0] || { stock_no: input, name: input, unknown: true };
  }
  const rows = await sb('GET', `stocks?name=eq.${encodeURIComponent(input)}&select=stock_no,name`);
  return rows[0] || null;
}

async function handleAdd(member, cmd) {
  const stock = await resolveStock(cmd.stock);
  if (!stock) {
    return `❌ 找不到「${cmd.stock}」。請確認名稱（公司簡稱），或直接輸入代號，例如：新增2330`;
  }
  await sb('POST', 'holdings', {
    member_id: member.id,
    stock_no: stock.stock_no,
    shares: cmd.shares === null ? 0 : cmd.shares,
    cost_price: cmd.cost,
  });
  const detail =
    cmd.shares === null
      ? '（觀察，未記股數）'
      : `${cmd.shares} 股${cmd.cost === null ? '' : `＠${cmd.cost}`}`;
  const warning = stock.unknown ? '\n⚠️ 代號不在上市對照表中，報價可能查不到（上櫃股票暫不支援）' : '';
  return `✅ 已為 ${member.name} 新增 ${stock.name}（${stock.stock_no}）${detail}${warning}`;
}

async function handleRemove(member, stockInput) {
  const stock = await resolveStock(stockInput);
  const stockNo = stock ? stock.stock_no : stockInput;
  const deleted = await sb('DELETE', `holdings?member_id=eq.${member.id}&stock_no=eq.${encodeURIComponent(stockNo)}`);
  if (!deleted.length) {
    return `❌ ${member.name} 沒有「${stockInput}」的紀錄。`;
  }
  return `🗑 已刪除 ${member.name} 的 ${stock ? `${stock.name}（${stockNo}）` : stockNo}，共 ${deleted.length} 筆。`;
}

// TWSE STOCK_DAY：以當月任一天查整月資料，月初無資料時 fallback 上個月
async function fetchTwseClose(stockNo) {
  const now = new Date();
  for (let monthsBack = 0; monthsBack <= 1; monthsBack++) {
    const d = new Date(now.getFullYear(), now.getMonth() - monthsBack, 1);
    const date = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}01`;
    try {
      const res = await helpers.httpRequest({
        method: 'GET',
        url: 'https://www.twse.com.tw/exchangeReport/STOCK_DAY',
        qs: { response: 'json', date, stockNo },
        json: true,
      });
      const parsed = parseTwseClose(res);
      if (parsed) {
        return parsed;
      }
    } catch (error) {
      // 該月查詢失敗，嘗試上個月
    }
  }
  return null;
}

async function handleList(member) {
  const rows = await sb('GET', `holdings?member_id=eq.${member.id}&select=stock_no,shares,cost_price`);
  if (!rows.length) {
    return `${member.name} 目前沒有任何持股，輸入「新增2330」開始記錄。`;
  }
  const aggregated = aggregateHoldings(rows);
  const codes = aggregated.map((a) => a.stockNo);
  const stockRows = await sb('GET', `stocks?stock_no=in.(${codes.map(encodeURIComponent).join(',')})&select=stock_no,name`);
  const nameMap = new Map(stockRows.map((s) => [s.stock_no, s.name]));
  const entries = [];
  for (const agg of aggregated) {
    entries.push({
      ...agg,
      name: nameMap.get(agg.stockNo) || agg.stockNo,
      quote: await fetchTwseClose(agg.stockNo),
    });
  }
  return formatPortfolio(member.name, entries);
}

async function handleCommand(lineUserId, cmd) {
  if (!lineUserId) {
    return HELP_TEXT;
  }
  if (cmd.action === 'login') {
    return handleLogin(lineUserId, cmd.name);
  }
  if (cmd.action === 'switch') {
    return handleSwitch(lineUserId, cmd.name);
  }
  if (cmd.action === 'help') {
    return HELP_TEXT;
  }
  const member = await getActingMember(lineUserId);
  if (!member) {
    return '👋 請先輸入「登入你的名字」開始使用，例如：登入dada';
  }
  if (cmd.action === 'add') {
    return handleAdd(member, cmd);
  }
  if (cmd.action === 'remove') {
    return handleRemove(member, cmd.stock);
  }
  if (cmd.action === 'list') {
    return handleList(member);
  }
  return HELP_TEXT;
}

async function replyLine(replyToken, text) {
  await helpers.httpRequest({
    method: 'POST',
    url: 'https://api.line.me/v2/bot/message/reply',
    headers: { Authorization: `Bearer ${ENV.LINE_CHANNEL_ACCESS_TOKEN}` },
    body: { replyToken, messages: [{ type: 'text', text: String(text).slice(0, 4900) }] },
    json: true,
  });
}

// ---------- 入口：驗簽章並逐一處理 events ----------
const item = $input.first();
const headers = item.json.headers || {};

let rawBody;
try {
  rawBody = await helpers.getBinaryDataBuffer(0, 'data');
} catch (error) {
  // rawBody 選項未啟用時的備援（簽章驗證可能因序列化差異失敗）
  rawBody = Buffer.from(JSON.stringify(item.json.body || {}), 'utf8');
}

const signature = headers['x-line-signature'];
const expected = crypto.createHmac('sha256', ENV.LINE_CHANNEL_SECRET).update(rawBody).digest('base64');
if (!signature || signature !== expected) {
  return [{ json: { ok: false, reason: 'invalid line signature' } }];
}

const payload = JSON.parse(rawBody.toString('utf8'));
const results = [];
for (const event of payload.events || []) {
  if (event.type !== 'message' || !event.message || event.message.type !== 'text') {
    continue;
  }
  const lineUserId = event.source ? event.source.userId : null;
  const cmd = parseCommand(event.message.text);
  let reply;
  try {
    reply = await handleCommand(lineUserId, cmd);
  } catch (error) {
    reply = `⚠️ 系統錯誤：${error.message}`;
  }
  try {
    await replyLine(event.replyToken, reply);
  } catch (error) {
    results.push({ json: { ok: false, reason: `reply failed: ${error.message}`, cmd } });
    continue;
  }
  results.push({ json: { ok: true, lineUserId, cmd, reply } });
}

return results.length ? results : [{ json: { ok: true, events: 0 } }];
