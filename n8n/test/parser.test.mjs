import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  parseCommand,
  parseTwseClose,
  formatRocDate,
  aggregateHoldings,
  formatPortfolio,
} = require('../src/parser.js');

test('parseCommand: 登入（含空白與不含空白）', () => {
  assert.deepEqual(parseCommand('登入dada'), { action: 'login', name: 'dada' });
  assert.deepEqual(parseCommand('登入 dada'), { action: 'login', name: 'dada' });
});

test('parseCommand: 切換', () => {
  assert.deepEqual(parseCommand('切換媽媽'), { action: 'switch', name: '媽媽' });
});

test('parseCommand: 新增代號（純觀察）', () => {
  assert.deepEqual(parseCommand('新增2330'), { action: 'add', stock: '2330', shares: null, cost: null });
});

test('parseCommand: 新增名稱', () => {
  assert.deepEqual(parseCommand('新增 緯創'), { action: 'add', stock: '緯創', shares: null, cost: null });
});

test('parseCommand: 新增含股數與成本', () => {
  assert.deepEqual(parseCommand('新增2330 1000 850'), { action: 'add', stock: '2330', shares: 1000, cost: 850 });
  assert.deepEqual(parseCommand('新增2330 1000'), { action: 'add', stock: '2330', shares: 1000, cost: null });
  assert.deepEqual(parseCommand('新增2330 1000 850.5'), { action: 'add', stock: '2330', shares: 1000, cost: 850.5 });
});

test('parseCommand: 刪除', () => {
  assert.deepEqual(parseCommand('刪除2330'), { action: 'remove', stock: '2330' });
});

test('parseCommand: 查詢清單', () => {
  assert.deepEqual(parseCommand('我的股票'), { action: 'list' });
  assert.deepEqual(parseCommand('清單'), { action: 'list' });
});

test('parseCommand: 無法辨識時回 help', () => {
  assert.equal(parseCommand('哈囉').action, 'help');
  assert.equal(parseCommand('').action, 'help');
  assert.equal(parseCommand('新增2330 abc').action, 'help');
});

test('parseTwseClose: 取最後一筆收盤價並清洗千分位', () => {
  const api = {
    stat: 'OK',
    data: [
      ['115/06/01', '60,942,792', '…', '2,355.00', '2,415.00', '2,350.00', '2,355.00', '0.00', '136,367'],
      ['115/06/02', '50,000,000', '…', '2,360.00', '2,420.00', '2,355.00', '2,400.00', '+45.00', '120,000'],
    ],
  };
  assert.deepEqual(parseTwseClose(api), { date: '115/06/02', close: 2400 });
});

test('parseTwseClose: 收盤價為 -- 時往前找', () => {
  const api = {
    stat: 'OK',
    data: [
      ['115/06/01', '1', '1', '10', '10', '10', '10.50', '0', '1'],
      ['115/06/02', '0', '0', '--', '--', '--', '--', ' 0.00', '0'],
    ],
  };
  assert.deepEqual(parseTwseClose(api), { date: '115/06/01', close: 10.5 });
});

test('parseTwseClose: stat 非 OK 或無資料回 null', () => {
  assert.equal(parseTwseClose({ stat: '查詢日期大於今日，請重新查詢!', total: 0 }), null);
  assert.equal(parseTwseClose({ stat: 'OK', data: [] }), null);
  assert.equal(parseTwseClose(null), null);
});

test('formatRocDate: 民國日期轉顯示格式', () => {
  assert.equal(formatRocDate('115/07/02'), '07/02');
  assert.equal(formatRocDate('bad'), 'bad');
});

test('aggregateHoldings: 依代號彙總股數與成本', () => {
  const rows = [
    { stock_no: '2330', shares: 1000, cost_price: 850 },
    { stock_no: '2330', shares: 500, cost_price: 900 },
    { stock_no: '3231', shares: 0, cost_price: null },
  ];
  const agg = aggregateHoldings(rows);
  assert.equal(agg.length, 2);
  const tsmc = agg.find((a) => a.stockNo === '2330');
  assert.equal(tsmc.shares, 1500);
  assert.equal(tsmc.cost, 1000 * 850 + 500 * 900);
  const wistron = agg.find((a) => a.stockNo === '3231');
  assert.equal(wistron.shares, 0);
  assert.equal(wistron.cost, 0);
});

test('formatPortfolio: 含損益、觀察中與總計', () => {
  const text = formatPortfolio('dada', [
    { stockNo: '2330', name: '台積電', shares: 1000, cost: 850000, quote: { date: '115/07/02', close: 2355 } },
    { stockNo: '3231', name: '緯創', shares: 0, cost: 0, quote: { date: '115/07/02', close: 100 } },
  ]);
  assert.match(text, /dada 的持股/);
  assert.match(text, /2330 台積電/);
  assert.match(text, /收盤 2,355（07\/02）/);
  assert.match(text, /1,000 股｜市值 2,355,000｜損益 \+1,505,000（\+177\.1%）/);
  assert.match(text, /觀察中（未記股數）/);
  assert.match(text, /總市值 2,355,000｜總損益 \+1,505,000/);
});

test('formatPortfolio: 查無報價的股票顯示警告', () => {
  const text = formatPortfolio('dada', [
    { stockNo: '6488', name: '環球晶', shares: 100, cost: 0, quote: null },
  ]);
  assert.match(text, /6488 環球晶　⚠️ 查無報價/);
});
