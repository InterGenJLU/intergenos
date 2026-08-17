// InterGen Operations Dashboard — dashboard.js
// Governance tab: tier bar, hash status, cooldowns, commandments, decisions
// Performance tab: latency bars, token throughput, model perf
// Usage tab: volume, query types, top tools, escalation
// Health tab: system health HUD

(function () {
  'use strict';

  const WS_URL = 'ws://localhost:8089/ws';

  let ws = null;
  let connected = false;
  let authToken = null;
  let currentTab = 'governance';
  let governanceData = {};
  let metricsData = {};
  let healthData = {};
  let activeCooldowns = 0;

  // ── WebSocket ──────────────────────────────────────────────────────────
  function connect() {
    let token = window.__INTERGEN_TOKEN__;
    if (!token) {
      token = sessionStorage.getItem('intergen_web_token');
      if (!token) {
        token = Array.from(crypto.getRandomValues(new Uint8Array(32)),
          b => b.toString(16).padStart(2, '0')).join('');
        sessionStorage.setItem('intergen_web_token', token);
      }
    }
    authToken = token;
    ws = new WebSocket(
      `${WS_URL}?source_interface=web`,
      ['intergen', 'bearer.' + token]
    );

    ws.onopen = () => {
      connected = true;
      loadAllData();
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        handleMsg(msg);
      } catch (_) {}
    };

    ws.onclose = () => { connected = false; };
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function loadAllData() {
    if (!connected) return;
    send({ type: 'request_governance' });
    send({ type: 'request_metrics' });
    send({ type: 'request_health' });
    // Also fetch real-time metrics via REST
    fetch('/api/metrics/performance', {
      headers: { 'Authorization': 'Bearer ' + authToken }
    }).then(r => r.json()).then(d => {
      updatePerformanceTab(d);
    }).catch(() => {});
    fetch('/api/metrics/usage', {
      headers: { 'Authorization': 'Bearer ' + authToken }
    }).then(r => r.json()).then(d => {
      updateUsageTab(d);
    }).catch(() => {});
  }

  function handleMsg(msg) {
    switch (msg.type) {
      case 'governance_report':
        governanceData = msg;
        updateGovernanceTab(msg);
        break;
      case 'metrics_report':
        metricsData = msg;
        updateMetricsSummary(msg);
        break;
      case 'health_report':
        healthData = msg;
        renderHealthReport(msg);
        break;
      case 'system_status':
        governanceData = msg.governance || governanceData;
        metricsData = msg.metrics || metricsData;
        updateGovernanceTab(governanceData);
        updateTabDots(msg);
        break;
    }
  }

  function updateTabDots(msg) {
    const gov = msg.governance || {};
    const health = msg.health || {};
    setTabDot('governance', gov.hash_verified ? 'green' : 'red');
    setTabDot('performance', 'green');
    setTabDot('usage', 'green');
    setTabDot('health', (health.last_error == null) ? 'green' : 'yellow');
  }

  function setTabDot(tab, color) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (!btn) return;
    let dot = btn.querySelector('.tab-dot');
    if (!dot) {
      dot = document.createElement('span');
      dot.className = 'tab-dot';
      btn.prepend(dot);
    }
    dot.className = `tab-dot ${color}`;
  }

  // ── Tab switching ──────────────────────────────────────────────────────
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      const tabId = `tab-${btn.dataset.tab}`;
      const tab = document.getElementById(tabId);
      if (tab) tab.classList.add('active');
      currentTab = btn.dataset.tab;
      if (currentTab === 'providers') loadProviders();
    });
  });

  // Deep-link: /dashboard.html#providers opens that tab directly (the chat UI's
  // 📞 Escalation-settings icon links here). Reuses the tab-button click path.
  // Invoked AFTER connect() so authToken is set before loadProviders() fetches
  // (otherwise the deep-link fires with Bearer null -> 401 -> empty form).
  function openHashTab() {
    const want = (location.hash || '').replace('#', '');
    if (!want) return;
    const btn = document.querySelector(`.tab-btn[data-tab="${want}"]`);
    if (btn) btn.click();
  }

  document.getElementById('dashboard-close').addEventListener('click', () => {
    window.location.href = '/';
  });

  // ── Governance tab ────────────────────────────────────────────────────
  function updateGovernanceTab(data) {
    if (!data || !Object.keys(data).length) return;
    renderTierBar(data);
    renderHashStatus(data);
    renderCooldowns(data);
    renderCommandments(data);
    renderDecisions(data);
  }

  function renderTierBar(data) {
    const tiers = [
      { name: 'OBSERVE', num: 0, desc: 'read only' },
      { name: 'ADJUST', num: 1, desc: 'install pkgs' },
      { name: 'PROPOSE', num: 2, desc: 'system config' },
      { name: 'ARCHITECT', num: 3, desc: 'kernel config' },
      { name: 'OWNER', num: 4, desc: 'gov changes' },
    ];
    const current = data.autonomy_tier || 2;
    const currentName = data.autonomy_tier_name || 'PROPOSE';
    const display = document.getElementById('tier-display');
    display.innerHTML = `Current: <span class="tier-current">${esc(currentName)}</span> (${current}/4)`;

    const bar = document.getElementById('tier-bar');
    bar.innerHTML = '';
    for (const t of tiers) {
      const seg = document.createElement('div');
      seg.className = 'tier-segment';
      if (t.num === current) seg.classList.add('active');
      else if (t.num < current) seg.classList.add('unlocked');
      else seg.classList.add('locked');
      seg.innerHTML = `<span class="tier-name">${t.name}</span><span class="tier-desc">${t.desc}</span>`;
      if (t.num <= current) {
        seg.innerHTML += '<span class="check-mark">✓</span>';
      }
      seg.title = `${t.name}: ${t.desc} — ${t.num <= current ? 'Unlocked' : 'Locked (requires owner elevation)'}`;
      bar.appendChild(seg);
    }
  }

  function renderHashStatus(data) {
    const el = document.getElementById('hash-status');
    const ok = data.hash_verified;
    el.className = ok ? 'verified' : 'tampered';
    const sha = data.hash_path || '?';
    el.textContent = ok
      ? `◉ VERIFIED — governance.py unchanged`
      : `✗ HASH MISMATCH — TAMPER DETECTED. All autonomous actions suspended.`;
  }

  function renderCooldowns(data) {
    const list = document.getElementById('cooldown-list');
    list.innerHTML = '';
    const cooldowns = data.active_cooldowns || 0;
    if (cooldowns === 0) {
      list.innerHTML = '<p style="color:var(--text-dim);font-size:13px">No active cooldowns. All categories clear.</p>';
      return;
    }
    list.innerHTML = '<div style="background:var(--bg-card);border:1px dashed var(--text-ghost);border-radius:var(--radius-card);padding:12px;text-align:center"><p style="color:var(--text-dim);font-style:italic;margin:0">Cooldown detail wiring pending</p><span style="font-size:11px;color:var(--text-ghost)">F-DASH-1 — governance engine per-category enumeration not yet exposed</span></div>';
  }

  function renderCommandments(data) {
    const list = document.getElementById('commandment-list');
    list.innerHTML = '';
    const cmds = data.commandments || [];
    for (const c of cmds) {
      const row = document.createElement('div');
      row.className = 'cmd-row';
      const enf = c.enforcement === 'code_enforced' ? 'code' : 'prompt';
      row.innerHTML = `
        <span class="cmd-number">${c.num || '?'}.</span>
        <span class="cmd-title">${esc(c.title || '')}</span>
        <span class="cmd-enforcement ${enf}">[${enf}]</span>`;
      const body = document.createElement('div');
      body.className = 'cmd-body';
      body.textContent = c.text || '';
      row.addEventListener('click', () => {
        const wasExpanded = row.classList.contains('expanded');
        // Close all
        document.querySelectorAll('.cmd-row.expanded').forEach(r => r.classList.remove('expanded'));
        if (!wasExpanded) row.classList.add('expanded');
      });
      list.appendChild(row);
      list.appendChild(body);
    }
  }

  function renderDecisions(data) {
    const tbody = document.querySelector('#decisions-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const decisions = data.recent_decisions || [];
    if (!decisions.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="color:var(--text-ghost)">No decisions recorded yet.</td></tr>';
      return;
    }
    for (const d of decisions) {
      const tr = document.createElement('tr');
      const decCls = d.decision === 'allow_once' || d.decision === 'allow_conversation' ? 'allow'
        : d.decision === 'deny' ? 'deny' : 'execute';
      const provCls = d.provenance === 'user_direct' ? 'direct'
        : d.provenance === 'user_implied' ? 'implied' : 'ingress';
      tr.innerHTML = `
        <td>${esc(d.timestamp || '?')}</td>
        <td>${esc(d.tool_name || '?')}</td>
        <td class="decision-${decCls}">${esc(d.decision || '?')}</td>
        <td class="prov-${provCls}">${esc(d.provenance || '?')}</td>`;
      tbody.appendChild(tr);
    }
  }

  // ── Performance tab ────────────────────────────────────────────────────
  function updatePerformanceTab(data) {
    const latency = data.latency || {};
    const counts = data.counts || {};
    renderLatencyBars(latency, counts);
    renderThroughputBars(data);
    renderCacheCard(data.cache);
    renderModelPerfTable(data);
  }

  function renderLatencyBars(latency, counts) {
    const el = document.getElementById('latency-chart');
    const routes = [
      { key: 'keyword_ms', label: 'keyword', countKey: 'keyword', color: varAccent() },
      { key: 'cache_ms', label: 'cache', countKey: 'cache' },
      { key: 'semantic_ms', label: 'semantic', countKey: 'semantic' },
      { key: 'llm_tools_ms', label: 'llm_tools', countKey: 'llm_tools' },
      { key: 'llm_freeform_ms', label: 'llm_freeform', countKey: 'llm_freeform' },
    ];
    const values = routes.map(r => latency[r.key] || 4).filter(v => v > 0);
    const maxVal = Math.max(...values, 1);
    el.innerHTML = '';
    for (const r of routes) {
      const val = latency[r.key] || 4;
      const cnt = counts[r.countKey] || 0;
      const pct = (val / maxVal) * 100;
      const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
      const share = ((cnt / total) * 100).toFixed(0);
      el.innerHTML += `
        <div class="bar-row">
          <span class="bar-label">${r.label}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
          <span class="bar-value">${val.toFixed(1)}ms (${share}%)</span>
        </div>`;
    }
  }

  function renderThroughputBars(data) {
    const el = document.getElementById('throughput-chart');
    // Cumulative authoritative token usage (llama.cpp tokenizer counts).
    const t = data.tokens || { prompt: 0, completion: 0, total: 0 };
    const counts = data.counts || {};
    const llm = counts.llm_calls || 0;
    const maxT = Math.max(t.prompt, t.completion, 1);
    const fmt = (n) => (n || 0).toLocaleString();
    el.innerHTML = `
      <div class="bar-row">
        <span class="bar-label">prompt tok</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(t.prompt / maxT) * 100}%"></div></div>
        <span class="bar-value">${fmt(t.prompt)}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">output tok</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(t.completion / maxT) * 100}%"></div></div>
        <span class="bar-value">${fmt(t.completion)}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">total tok</span>
        <div class="bar-track"><div class="bar-fill" style="width:100%"></div></div>
        <span class="bar-value">${fmt(t.total)}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">llm calls</span>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.min(llm / 10 * 100, 100)}%"></div></div>
        <span class="bar-value">${llm}</span>
      </div>`;
  }

  function renderCacheCard(cache) {
    const el = document.getElementById('cache-chart');
    if (!el) return;
    const c = cache || { hits: 0, misses: 0, hit_rate: 0 };
    const total = c.hits + c.misses;
    el.innerHTML = `
      <div class="bar-row">
        <span class="bar-label">hit rate</span>
        <div class="bar-track"><div class="bar-fill" style="width:${c.hit_rate}%"></div></div>
        <span class="bar-value">${c.hit_rate}%</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">hits</span>
        <div class="bar-track"><div class="bar-fill" style="width:${total ? c.hits / total * 100 : 0}%"></div></div>
        <span class="bar-value">${c.hits}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">misses</span>
        <div class="bar-track"><div class="bar-fill" style="width:${total ? c.misses / total * 100 : 0}%"></div></div>
        <span class="bar-value">${c.misses}</span>
      </div>`;
  }

  function renderModelPerfTable(data) {
    const tbody = document.querySelector('#model-perf-table tbody');
    if (!tbody) return;
    const m = data.model_perf;
    if (!m || !m.calls) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-ghost);font-size:13px;padding:10px">No model calls yet — ask InterGen something to populate this.</td></tr>';
      return;
    }
    // Columns: Tier | Model | Requests | Avg TTFT | p95 Latency. Values are
    // real llama.cpp timings (prompt-eval ms ~= TTFT; p95 of total per-call ms).
    tbody.innerHTML = `<tr>
      <td>local</td>
      <td>${esc(m.model || 'local')}</td>
      <td>${m.calls}</td>
      <td>${(m.avg_ttft_ms || 0).toFixed(0)}ms</td>
      <td>${(m.p95_latency_ms || 0).toFixed(0)}ms</td>
    </tr>`;
  }

  // ── Usage tab ──────────────────────────────────────────────────────────
  function updateUsageTab(data) {
    renderVolumeChart(data);
    renderQueryTypeChart(data);
    renderTopTools(data);
    renderEscalationChart(data);
  }

  function renderVolumeChart(data) {
    const el = document.getElementById('volume-chart');
    const total = data.requests || 0;
    el.innerHTML = `
      <p style="font-family:var(--font-mono);color:var(--text)">Total requests: <strong>${total}</strong></p>
      <p style="color:var(--text-dim);font-size:13px;margin-top:4px">
        Sparkline data aggregated from metrics tracker.
        Real-time charts require WebSocket metrics_update messages (60s interval).
      </p>`;
  }

  function renderQueryTypeChart(data) {
    const el = document.getElementById('query-type-chart');
    // Backend supplies query_types: {diagnostic, general, identity, safety, ...}
    // from the router's qtype:<type> counters. Stable order, unseen types at 0.
    const qt = data.query_types || {};
    const order = ['diagnostic', 'general', 'identity', 'safety'];
    const keys = order.concat(Object.keys(qt).filter(k => !order.includes(k)));
    const total = Math.max(keys.reduce((s, k) => s + (qt[k] || 0), 0), 1);
    el.innerHTML = '';
    for (const k of keys) {
      const val = qt[k] || 0;
      el.innerHTML += `
        <div class="bar-row">
          <span class="bar-label">${esc(k)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${(val/total)*100}%"></div></div>
          <span class="bar-value">${val} (${((val/total)*100).toFixed(0)}%)</span>
        </div>`;
    }
  }

  function renderTopTools(data) {
    const el = document.getElementById('top-tools-chart');
    const counts = data.tool_counts || {};
    const tools = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 7);
    const maxCnt = Math.max(...tools.map(t => t[1]), 1);
    el.innerHTML = '';
    for (const [name, cnt] of tools) {
      el.innerHTML += `
        <div class="bar-row">
          <span class="bar-label">${esc(name)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${(cnt/maxCnt)*100}%"></div></div>
          <span class="bar-value">${cnt}</span>
        </div>`;
    }
    if (!tools.length) {
      el.innerHTML = '<p style="color:var(--text-ghost);font-size:13px">No tool usage data yet.</p>';
    }
  }

  function renderEscalationChart(data) {
    const el = document.getElementById('escalation-chart');
    el.innerHTML = `
      <p style="color:var(--text-dim);font-size:13px">
        Escalations: ${data.escalations || 0} total
        <br><span style="font-style:italic;color:var(--text-ghost)">Breakdown wiring pending — F-DASH-1</span>
      </p>`;
  }

  // ── Health tab ─────────────────────────────────────────────────────────
  function renderHealthReport(data) {
    const el = document.getElementById('health-report');
    if (!el) return;
    const layers = data.layers || [];
    let html = '';
    for (const layer of layers) {
      html += `<div class="health-layer"><div class="health-layer-title">${esc(layer.name || '')}</div>`;
      for (const check of layer.checks || []) {
        const dotCls = check.status || 'green';
        html += `<div class="health-check">
          <span class="health-dot ${dotCls}">●</span>
          <span class="health-name">${esc(check.name || '')}</span>
          <span class="health-summary">${esc(check.summary || '')}</span>
        </div>`;
      }
      html += '</div>';
    }
    const s = data.summary || {};
    html += `<div class="summary-badge">
      <span class="sb-green">${s.green || 0} ●</span>
      <span class="sb-yellow">${s.yellow || 0} ●</span>
      <span class="sb-red">${s.red || 0} ●</span>
    </div>`;
    el.innerHTML = html;
  }

  // ── Metrics summary (for when metrics arrive via WS) ────────────────────
  function updateMetricsSummary(data) {
    updatePerformanceTab(data);
    updateUsageTab(data);
  }

  // ── Phone-a-friend provider config (§B) ─────────────────────────────────
  let providerCatalog = {};

  function apiAuth() { return { 'Authorization': 'Bearer ' + authToken }; }

  function loadProviders() {
    fetch('/api/providers', { headers: apiAuth() })
      .then(r => r.json()).then(renderProviders).catch(() => {});
  }

  function renderProviders(data) {
    providerCatalog = data.catalog || {};
    const listEl = document.getElementById('providers-list');
    if (listEl) {
      const providers = data.providers || [];
      if (!providers.length) {
        listEl.innerHTML = '<p style="color:var(--text-ghost);font-style:italic;font-size:13px;margin:0">No frontier provider configured. InterGen runs fully local until you add one below.</p>';
      } else {
        listEl.innerHTML = providers.map(p => {
          const isPrimary = p.name === data.primary;
          return `<div class="provider-row" style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
            <span style="flex:1">
              <strong>${esc(p.name)}</strong> ${isPrimary ? '<span class="provenance-badge direct" style="margin-left:6px">primary</span>' : ''}
              <br><span style="color:var(--text-dim);font-size:12px">${esc(p.adapter)} · ${esc(p.model)}</span>
            </span>
            ${isPrimary ? '' : `<button class="primary-btn prov-primary" data-name="${esc(p.name)}" style="padding:3px 8px;font-size:12px">Set primary</button>`}
            <button class="gate-btn deny prov-delete" data-name="${esc(p.name)}" style="padding:3px 8px;font-size:12px">Remove</button>
          </div>`;
        }).join('');
      }
    }
    renderProviderForm(data);
  }

  function renderProviderForm(data) {
    const el = document.getElementById('provider-form');
    if (!el) return;
    const cat = data.catalog || {};
    const adapters = data.available_adapters || Object.keys(cat);
    const opts = adapters.map(a => `<option value="${esc(a)}">${esc((cat[a] && cat[a].label) || a)}</option>`).join('');
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;max-width:520px">
        <label style="font-size:12px;color:var(--text-dim)">Provider
          <select id="pf-adapter" style="width:100%;margin-top:3px">${opts}</select></label>
        <label style="font-size:12px;color:var(--text-dim)">Name (label you'll see)
          <input id="pf-name" type="text" style="width:100%;margin-top:3px" placeholder="e.g. fable"></label>
        <label style="font-size:12px;color:var(--text-dim)">Model (API id)
          <input id="pf-model" type="text" style="width:100%;margin-top:3px"></label>
        <label style="font-size:12px;color:var(--text-dim)">API key <span id="pf-keyhint"></span>
          <input id="pf-key" type="password" autocomplete="off" style="width:100%;margin-top:3px" placeholder="stored in your system keyring, never in config"></label>
        <button id="pf-save" class="primary-btn" style="align-self:flex-start;margin-top:4px">Save provider</button>
      </div>`;
    const adapterSel = document.getElementById('pf-adapter');
    const apply = () => {
      const a = adapterSel.value;
      const c = cat[a] || {};
      const nameEl = document.getElementById('pf-name');
      const modelEl = document.getElementById('pf-model');
      if (!nameEl.value) nameEl.value = a;
      modelEl.value = c.default_model || '';
      document.getElementById('pf-keyhint').innerHTML = c.key_url
        ? `— <a href="${esc(c.key_url)}" target="_blank" rel="noopener" style="color:var(--accent)">get a key</a>` : '';
    };
    adapterSel.addEventListener('change', apply);
    apply();
    document.getElementById('pf-save').addEventListener('click', saveProvider);
  }

  function saveProvider() {
    const msg = document.getElementById('provider-form-msg');
    const body = {
      adapter: document.getElementById('pf-adapter').value,
      name: document.getElementById('pf-name').value.trim(),
      model: document.getElementById('pf-model').value.trim(),
      api_key: document.getElementById('pf-key').value,
    };
    msg.textContent = 'Saving...'; msg.style.color = 'var(--text-dim)';
    fetch('/api/providers', {
      method: 'POST',
      headers: { ...apiAuth(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(async r => {
      const d = await r.json();
      if (!r.ok) { msg.textContent = d.error || 'Save failed.'; msg.style.color = 'var(--warning)'; return; }
      msg.textContent = 'Saved.'; msg.style.color = 'var(--accent)';
      document.getElementById('pf-key').value = '';
      renderProviders(d);
    }).catch(() => { msg.textContent = 'Save failed.'; msg.style.color = 'var(--warning)'; });
  }

  document.getElementById('providers-list').addEventListener('click', (e) => {
    const setBtn = e.target.closest('.prov-primary');
    const delBtn = e.target.closest('.prov-delete');
    if (setBtn) {
      fetch('/api/providers/primary', {
        method: 'POST', headers: { ...apiAuth(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: setBtn.dataset.name }),
      }).then(r => r.json()).then(renderProviders).catch(() => {});
    } else if (delBtn) {
      fetch('/api/providers/' + encodeURIComponent(delBtn.dataset.name), {
        method: 'DELETE', headers: apiAuth(),
      }).then(r => r.json()).then(renderProviders).catch(() => {});
    }
  });

  // ── Re-verify button ───────────────────────────────────────────────────
  document.getElementById('btn-reverify').addEventListener('click', () => {
    send({ type: 'request_governance' });
    const el = document.getElementById('btn-reverify');
    el.textContent = 'Verifying...';
    el.disabled = true;
    setTimeout(() => { el.textContent = 'Re-verify'; el.disabled = false; }, 3000);
  });

  // ── Refresh on interval ────────────────────────────────────────────────
  setInterval(() => {
    if (connected) {
      send({ type: 'request_governance' });
      send({ type: 'request_metrics' });
      send({ type: 'request_health' });
    }
  }, 60000);

  // ── Helpers ────────────────────────────────────────────────────────────
  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function varAccent() {
    return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  }

  // ── Init ───────────────────────────────────────────────────────────────
  connect();        // sets authToken synchronously
  openHashTab();    // deep-link AFTER authToken is set
})();