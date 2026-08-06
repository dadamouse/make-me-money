# 全市場融資維持率加入總經（盤前導航）設計

日期：2026-08-06｜狀態：已由 dada 核准（方案 A：官方資料自算）

## 目標

把「融資維持率」指標（參考永豐金 richclub 文章：130% 追繳、166% 融資起始）以
**全市場版本**加入盤前導航的總經段，量化散戶槓桿的斷頭風險。

## 指標定義

```
大盤融資維持率(%) = Σ(每檔上市融資餘額張數 × 1000 × 收盤價) ÷ (上市融資餘額金額) × 100
```

- 分子（擔保品市值）：自家 DB `daily_margins.margin_balance` × `daily_closes.close`，
  僅計上市（`stocks.market = '上市'`，與加權指數對應）。
- 分母（融資金額）：TWSE 信用交易統計彙總
  `https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=YYYYMMDD&selectType=MS&response=json`
  的「融資金額(仟元)」今日餘額。
- 已知近似誤差：擔保品以收盤價估、不含現金抵繳——業界通用近似，可接受。

## 元件

1. **TWSE 抓取**（`app/twse.py`）：`fetch_credit_summary(date)` 回傳 rwd JSON。
   解析函式放 `app/snapshot.py`：從 tables 取「融資金額(仟元)」列的今日餘額（仟元）。
2. **DB**（`supabase/migrations/20260806_market_margin.sql`）：
   - 表 `market_margin(trade_date date pk, financing_amount numeric, collateral_value numeric, maintenance_pct numeric)`（金額單位：仟元）。
   - RPC `margin_collateral_value(p_trade_date date)`：回傳上市擔保品市值合計（仟元）。
3. **快照**（`app/snapshot.py` → `run_snapshot`）：融資融券 upsert 後，
   取 margin rows 的最新 trade_date → 抓彙總、呼叫 RPC、算維持率、upsert `market_margin`。
   任一步失敗記 log 跳過（不影響其他快照）；當日資料未公布（stat != OK）視為正常跳過。
4. **盤前導航**（`app/premarket.py`）：
   - 數據行：`融資維持率 168.2%（前日 167.8%）`（讀 `market_margin` 最新 2 列）。
   - 白話解讀（僅在對應區間出現）：
     - `< 150%`：全市場逼近斷頭區，恐慌賣壓一觸即發
     - `150–160%`：追繳壓力浮現，反彈易被融資賣壓蓋掉
     - `≥ 170%`：槓桿在安全水位，散戶部位健康
     - `160–170%`：中性，不顯示解讀行

## 錯誤處理

- TWSE 彙總 API 失敗／未公布 → 跳過當日 `market_margin`，盤前導航沒有資料列就不顯示。
- RPC 回空（快照不完整）→ 同上。

## 測試

- 解析：彙總 JSON fixture → 融資金額。
- 快照：`/admin/daily-snapshot` 後 `market_margin` 有列且 pct 正確（mock TWSE 彙總 URL＋RPC fixture）。
- 盤前：seed `market_margin` 兩列 → morning-open 推播文字含維持率行與對應解讀。
