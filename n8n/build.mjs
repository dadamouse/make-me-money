// 由 src/ 產生可匯入的 n8n workflow JSON。
// 執行：node n8n/build.mjs（輸出 n8n/line-stock-bot.json、n8n/sync-stock-list.json）
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(join(here, 'src', name), 'utf8');

const parserSource = read('parser.js');

function codeNode(name, jsCode, position) {
  return {
    parameters: { mode: 'runOnceForAllItems', language: 'javaScript', jsCode },
    id: name.replace(/\s+/g, '-').toLowerCase(),
    name,
    type: 'n8n-nodes-base.code',
    typeVersion: 2,
    position,
  };
}

const lineStockBot = {
  name: 'line-stock-bot',
  nodes: [
    {
      parameters: {
        httpMethod: 'POST',
        path: 'line-stock',
        responseMode: 'onReceived',
        options: { rawBody: true },
      },
      id: 'line-webhook',
      name: 'LINE Webhook',
      type: 'n8n-nodes-base.webhook',
      typeVersion: 2,
      position: [0, 0],
      webhookId: 'c9b1a7de-4f52-4b1e-9f3a-2f6c1d8e0a01',
    },
    codeNode('處理指令並回覆', `${parserSource}\n${read('line-stock-bot.main.js')}`, [240, 0]),
  ],
  connections: {
    'LINE Webhook': {
      main: [[{ node: '處理指令並回覆', type: 'main', index: 0 }]],
    },
  },
  settings: { executionOrder: 'v1' },
};

const syncStockList = {
  name: 'sync-stock-list',
  nodes: [
    {
      parameters: {},
      id: 'manual-trigger',
      name: '手動執行',
      type: 'n8n-nodes-base.manualTrigger',
      typeVersion: 1,
      position: [0, 0],
    },
    codeNode('匯入上市股票對照表', read('sync-stock-list.main.js'), [240, 0]),
  ],
  connections: {
    手動執行: {
      main: [[{ node: '匯入上市股票對照表', type: 'main', index: 0 }]],
    },
  },
  settings: { executionOrder: 'v1' },
};

for (const [file, workflow] of [
  ['line-stock-bot.json', lineStockBot],
  ['sync-stock-list.json', syncStockList],
]) {
  writeFileSync(join(here, file), `${JSON.stringify(workflow, null, 2)}\n`);
  console.log(`wrote n8n/${file}`);
}
