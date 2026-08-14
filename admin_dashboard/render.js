// 渲染逻辑：账号 / 媒体库 / 调用 4 卡 / 按模型统计。数据来自 window.__DATA__。
const D = window.__DATA__;
const PERIOD_SEC = { '24h': 86400, '7d': 604800, '30d': 2592000, '90d': 7776000 };
const PERIOD_LABEL = { '24h': '24 小时', '7d': '7 天', '30d': '30 天', '90d': '90 天' };
let P = D.defaultPeriod || '7d';

const fmt = n => (n == null ? 0 : n).toLocaleString();
const pct = (a, b) => (b ? (a / b * 100) : 0).toFixed(1) + '%';
const dur = ms => (ms == null ? '0.0' : (ms / 1000).toFixed(1)) + 's';
const usd = ticks => '$' + ((ticks || 0) / 1e8).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = b;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (v >= 10 ? Math.round(v) : v.toFixed(1)) + ' ' + u[i];
}

// grok2api 自身口径：status 2xx 且 errorCode 为空
const isOk = it => it.statusCode >= 200 && it.statusCode < 300 && !it.errorCode;

// ---------- 账号 ----------
function renderAccounts() {
  const a = D.accounts || {};
  const prov = a.providers || {};
  const defs = [['grok_build', 'Grok Build'], ['grok_web', 'Grok Web'], ['grok_console', 'Grok Console']];
  let html = '';
  for (const [key, label] of defs) {
    const p = prov[key] || {};
    const total = p.total || 0;
    const avail = p.available == null ? total : p.available;
    html += '<div class="card"><div class="label">' + label + '</div>'
      + '<div class="big">' + fmt(total) + '</div>'
      + '<div class="sub">可用 ' + fmt(avail) + ' · ' + pct(avail, total) + '</div></div>';
  }
  html += '<div class="card"><div class="label">全部账号</div>'
    + '<div class="big">' + fmt(a.total || 0) + '</div>'
    + '<div class="sub">可用 ' + fmt(a.available || 0) + ' · 恢复中 ' + fmt(a.recovering || 0) + '</div></div>';
  document.getElementById('accountCards').innerHTML = html;

  const rec = a.recovery || {}, iss = a.issues || {};
  const chips = [
    ['冷却', rec.cooldown || 0, 'warn'],
    ['待重置', rec.waitingReset || 0, 'warn'],
    ['探测中', rec.probing || 0, ''],
    ['风控', a.risk || 0, 'bad'],
    ['停用', iss.disabled || 0, 'bad'],
    ['失效', iss.reauthRequired || 0, 'warn'],
  ];
  document.getElementById('accountIssues').innerHTML =
    '<div class="label">异常账号明细<span class="meta"> · 风控仅统计 Build 渠道；停用+失效即 attention '
    + fmt(a.attention || 0) + '</span></div>'
    + '<div class="heal">' + chips.map(c =>
      '<span class="chip ' + (c[1] ? c[2] : 'zero') + '">' + c[0] + ' ' + fmt(c[1]) + '</span>'
    ).join('') + '</div>';
}

// ---------- 媒体库 ----------
function renderMedia() {
  const im = D.images || {};
  document.getElementById('imageCard').innerHTML =
    '<div class="label">图片库</div>'
    + '<div class="big">' + fmt(im.totalImages || 0) + ' <span class="unit">张</span></div>'
    + '<div class="sub">占用空间 ' + fmtBytes(im.totalBytes || 0) + '</div>';

  const v = D.videos || {};
  const parts = [['排队', v.queued || 0, 'warn'], ['进行中', v.inProgress || 0, 'warn'],
                 ['已完成', v.completed || 0, ''], ['失败', v.failed || 0, 'bad']];
  document.getElementById('videoCard').innerHTML =
    '<div class="label">视频库</div>'
    + '<div class="big">' + fmt(v.totalJobs || 0) + ' <span class="unit">个任务</span></div>'
    + '<div class="heal">' + parts.map(p =>
      '<span class="chip ' + (p[1] ? p[2] : 'zero') + '">' + p[0] + ' ' + fmt(p[1]) + '</span>'
    ).join('') + '</div>'
    + '<div class="sub" style="margin-top:6px">视频无服务端占用空间聚合</div>';
}

// ---------- 时间筛选 ----------
function renderPeriodRow() {
  const row = document.getElementById('periodRow');
  row.innerHTML = '';
  for (const k of Object.keys(PERIOD_SEC)) {
    if (!D.periods[k]) continue;
    const b = document.createElement('button');
    b.className = 'btn' + (k === P ? ' act' : '');
    b.textContent = PERIOD_LABEL[k];
    b.onclick = () => { P = k; renderPeriodRow(); renderCalls(); renderModels(); };
    row.appendChild(b);
  }
}

// ---------- 调用 4 卡 ----------
function renderCalls() {
  const s = D.periods[P] || {};
  const u = s.usage || {}, pr = s.pricing || {};
  document.getElementById('callCards').innerHTML =
    '<div class="card"><div class="label">总请求</div>'
    + '<div class="big">' + fmt(u.requests || 0) + '</div>'
    + '<div class="sub">成功 <span class="good">' + fmt(u.successfulRequests || 0)
    + '</span> · 失败 <span class="bad">' + fmt(u.failedRequests || 0) + '</span></div></div>'

    + '<div class="card"><div class="label">总 Tokens</div>'
    + '<div class="big">' + fmt(u.totalTokens || 0) + '</div>'
    + '<div class="sub">输入 ' + fmt(u.inputTokens || 0) + '（缓存 ' + fmt(u.cachedInputTokens || 0)
    + '） · 输出 ' + fmt(u.outputTokens || 0) + ' · 推理 ' + fmt(u.reasoningTokens || 0) + '</div></div>'

    + '<div class="card"><div class="label">成功率</div>'
    + '<div class="big">' + (u.successRate == null ? 0 : u.successRate).toFixed(1) + '%</div>'
    + '<div class="sub">平均耗时 ' + dur(u.averageDurationMs) + '</div></div>'

    + '<div class="card"><div class="label">估算费用</div>'
    + '<div class="big">' + usd(u.estimatedCostInUsdTicks) + '</div>'
    + '<div class="sub">计价 ' + fmt(pr.pricedRequests || 0) + ' · 未计价 '
    + fmt(pr.unpricedRequests || 0) + '</div></div>';
}

// ---------- 按模型统计（本地分组） ----------
function renderModels() {
  const src = (D.auditByPeriod || {})[P] || [];
  const map = new Map();
  for (const it of src) {
    const k = it.modelPublicId || it.modelUpstreamModel || '(未知模型)';
    if (!map.has(k)) map.set(k, { n: 0, ok: 0, ms: 0, tok: 0 });
    const r = map.get(k);
    r.n++;
    if (isOk(it)) r.ok++;
    if (it.durationMs) r.ms += it.durationMs;
    if (it.totalTokens) r.tok += it.totalTokens;
  }
  const rows = [...map.entries()].sort((a, b) => b[1].n - a[1].n || b[1].ok - a[1].ok);
  const tb = document.getElementById('modelBody');
  document.getElementById('modelNote').textContent = '（' + PERIOD_LABEL[P] + '）';

  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted)">当前周期暂无请求</td></tr>';
    document.getElementById('modelFoot').textContent = '';
    return;
  }
  tb.innerHTML = rows.map(([k, r]) =>
    '<tr><td>' + k + '</td><td>' + fmt(r.n) + '</td>'
    + '<td class="good">' + fmt(r.ok) + '</td>'
    + '<td class="' + (r.n - r.ok ? 'bad' : '') + '">' + fmt(r.n - r.ok) + '</td>'
    + '<td>' + pct(r.ok, r.n) + '</td><td>' + dur(r.ms / r.n) + '</td>'
    + '<td>' + fmt(r.tok) + '</td></tr>'
  ).join('');
  document.getElementById('modelFoot').textContent =
    '共 ' + rows.length + ' 个模型 / ' + src.length
    + ' 条逐条审计（cursor 全量拉取，成功口径：状态 2xx 且无 errorCode）';
}

// ---------- 初始 ----------
document.getElementById('gen').textContent = new Date(D.generatedAt).toLocaleString();
renderAccounts();
renderMedia();
renderPeriodRow();
renderCalls();
renderModels();
