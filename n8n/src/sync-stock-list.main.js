// n8n Code node：抓 TWSE 上市公司基本資料，upsert 進 Supabase stocks 對照表。
// 手動執行一次即可，之後每月跑一次更新（新上市公司）。
// 依賴環境變數：SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY

const helpers = this.helpers;

const SUPABASE_URL = $env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = $env.SUPABASE_SERVICE_ROLE_KEY;
if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
  throw new Error('缺少環境變數 SUPABASE_URL 或 SUPABASE_SERVICE_ROLE_KEY，請到 HF Space Settings 設定');
}

const companies = await helpers.httpRequest({
  method: 'GET',
  url: 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
  json: true,
});
if (!Array.isArray(companies) || companies.length === 0) {
  throw new Error('TWSE OpenAPI 回傳空資料，請稍後再試');
}

const rows = companies
  .filter((c) => c['公司代號'] && c['公司簡稱'])
  .map((c) => ({
    stock_no: String(c['公司代號']).trim(),
    name: String(c['公司簡稱']).trim(),
  }));

const CHUNK_SIZE = 500;
for (let i = 0; i < rows.length; i += CHUNK_SIZE) {
  await helpers.httpRequest({
    method: 'POST',
    url: `${SUPABASE_URL}/rest/v1/stocks?on_conflict=stock_no`,
    headers: {
      apikey: SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'resolution=merge-duplicates',
    },
    body: rows.slice(i, i + CHUNK_SIZE),
    json: true,
  });
}

return [{ json: { imported: rows.length } }];
