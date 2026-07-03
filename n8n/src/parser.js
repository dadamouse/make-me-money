// 純函式：指令解析、TWSE 回應解析、持股彙總與訊息格式化。
// 此檔會由 build.mjs 併入 n8n Code node，也供 node --test 單元測試使用。

const HELP_TEXT = [
  '📖 指令說明',
  '登入dada　　　　　建立/綁定身份',
  '切換媽媽　　　　　代操作家人帳戶',
  '新增2330　　　　　加入觀察（不記股數）',
  '新增2330 1000 850　記 1000 股、每股成本 850',
  '新增緯創　　　　　也可用公司簡稱',
  '刪除2330　　　　　刪除該檔所有紀錄',
  '我的股票　　　　　列出持股與損益',
].join('\n');

function parseCommand(rawText) {
  const text = String(rawText || '').trim();
  let m;
  if ((m = text.match(/^登入\s*(\S+)$/))) {
    return { action: 'login', name: m[1] };
  }
  if ((m = text.match(/^切換\s*(\S+)$/))) {
    return { action: 'switch', name: m[1] };
  }
  if ((m = text.match(/^新增\s*(\S+?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?$/))) {
    return {
      action: 'add',
      stock: m[1],
      shares: m[2] === undefined ? null : Number(m[2]),
      cost: m[3] === undefined ? null : Number(m[3]),
    };
  }
  if ((m = text.match(/^刪除\s*(\S+)$/))) {
    return { action: 'remove', stock: m[1] };
  }
  if (/^(我的股票|清單|列表)$/.test(text)) {
    return { action: 'list' };
  }
  return { action: 'help' };
}

// TWSE STOCK_DAY 回應 → 最新收盤價。
// data 每列：[日期(民國), 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 筆數]
function parseTwseClose(apiJson) {
  if (!apiJson || apiJson.stat !== 'OK' || !Array.isArray(apiJson.data) || apiJson.data.length === 0) {
    return null;
  }
  for (let i = apiJson.data.length - 1; i >= 0; i--) {
    const row = apiJson.data[i];
    const close = Number(String(row[6]).replace(/,/g, ''));
    if (Number.isFinite(close)) {
      return { date: String(row[0]), close };
    }
  }
  return null;
}

// 民國日期 '115/07/02' → 顯示用 '07/02'
function formatRocDate(rocDate) {
  const parts = String(rocDate || '').split('/');
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : String(rocDate || '');
}

// holdings 資料列（stock_no, shares, cost_price）→ 依代號彙總
function aggregateHoldings(rows) {
  const byStock = new Map();
  for (const row of rows || []) {
    const stockNo = String(row.stock_no);
    const prev = byStock.get(stockNo) || { stockNo, shares: 0, cost: 0 };
    const shares = Number(row.shares) || 0;
    const costPrice = row.cost_price === null || row.cost_price === undefined ? null : Number(row.cost_price);
    byStock.set(stockNo, {
      stockNo,
      shares: prev.shares + shares,
      cost: prev.cost + (costPrice !== null && shares > 0 ? shares * costPrice : 0),
    });
  }
  return [...byStock.values()];
}

function formatNumber(n) {
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

// entries: [{stockNo, name, shares, cost, quote: {date, close} | null}]
function formatPortfolio(memberName, entries) {
  const lines = [`📊 ${memberName} 的持股`];
  let totalValue = 0;
  let totalCost = 0;
  for (const e of entries) {
    if (!e.quote) {
      lines.push(`${e.stockNo} ${e.name}　⚠️ 查無報價（可能為上櫃或停牌）`);
      continue;
    }
    lines.push(`${e.stockNo} ${e.name}　收盤 ${formatNumber(e.quote.close)}（${formatRocDate(e.quote.date)}）`);
    if (e.shares > 0) {
      const value = e.shares * e.quote.close;
      totalValue += value;
      let pnlText = '';
      if (e.cost > 0) {
        totalCost += e.cost;
        const pnl = value - e.cost;
        const pct = (pnl / e.cost) * 100;
        pnlText = `｜損益 ${pnl >= 0 ? '+' : ''}${formatNumber(pnl)}（${pnl >= 0 ? '+' : ''}${pct.toFixed(1)}%）`;
      }
      lines.push(`　${formatNumber(e.shares)} 股｜市值 ${formatNumber(value)}${pnlText}`);
    } else {
      lines.push('　觀察中（未記股數）');
    }
  }
  if (totalValue > 0) {
    lines.push('─────────');
    let totalLine = `總市值 ${formatNumber(totalValue)}`;
    if (totalCost > 0) {
      const pnl = totalValue - totalCost;
      totalLine += `｜總損益 ${pnl >= 0 ? '+' : ''}${formatNumber(pnl)}`;
    }
    lines.push(totalLine);
  }
  return lines.join('\n');
}

if (typeof module !== 'undefined') {
  module.exports = {
    HELP_TEXT,
    parseCommand,
    parseTwseClose,
    formatRocDate,
    aggregateHoldings,
    formatPortfolio,
  };
}
