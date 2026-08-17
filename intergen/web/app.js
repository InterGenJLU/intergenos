// InterGen Web UI — app.js
// Preceding-project patterns: WebSocket client, streaming token-by-token,
// inline provenance gate, session sidebar, HUD stats bar, keyboard shortcuts.
// Zero dependencies — vanilla JavaScript, one protocol, one backend.

(function () {
  'use strict';

  // ── Constants ──────────────────────────────────────────────────────────
  const WS_URL = 'ws://localhost:8089/ws';
  const STATIC_URL = '/static/';
  const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000];
  const MARK_THINKING_ANIM = 'mark-pulse 1.2s ease-in-out infinite';
  const THINKING_DEFAULT = 'InterGen is thinking…';
  // G3-17: a sent message must reach a terminal state. If no server activity
  // (stream_start / response / error) arrives within this window the socket is
  // almost certainly half-open — the send was lost. We clear thinking, tell the
  // user plainly, and force a reconnect. Generous enough never to false-positive
  // on a legitimately slow first response (the daemon emits stream_start within
  // seconds of routing, even on a cold model).
  const RESPONSE_TIMEOUT_MS = 30000;

  // ── State ──────────────────────────────────────────────────────────────
  const state = {
    ws: null,
    connected: false,
    clientId: '',
    sourceInterface: 'web',
    sessionId: 'default',
    switchingTo: '',
    messages: [],
    streaming: false,
    streamingContent: '',
    turnId: '',
    typeTimer: null,
    pendingGate: null,
    modelTier: 'medium',
    systemStatus: {},
    governance: {},
    reconnectIdx: 0,
    reconnectTimer: null,
    imageData: null,
    imageName: '',
    inputHistory: [],
    historyIndex: null,
    historyDraft: '',
    pendingResponseTimer: null,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);
  const dom = {
    messages: $('#messages'),
    streamingContainer: $('#streaming-container'),
    streamingContent: $('#streaming-content'),
    thinking: $('#thinking-indicator'),
    thinkingText: $('#thinking-indicator .thinking-text'),
    userInput: $('#user-input'),
    btnSend: $('#btn-send'),
    // The header thinking-mark was retired — the brand now lives in the window
    // titlebar and the in-chat thinking pill is the activity indicator. Stub it
    // so the existing mark-glow calls are harmless no-ops (no element to glow).
    headerMark: $('#header-mark') || { style: {} },
    connectionStatus: $('#connection-status'),
    hudModel: $('#hud-model .hud-value'),
    hudTier: $('#hud-tier .hud-value'),
    hudContext: $('#hud-context .hud-value'),
    hudUptime: $('#hud-uptime .hud-value'),
    sessionList: $('#session-list'),
    sidebar: $('#sidebar'),
    sidebarToggle: $('#sidebar-toggle'),
    modalOverlay: $('#modal-overlay'),
    modalTitle: $('#modal-title'),
    modalContent: $('#modal-content'),
    modalClose: $('#modal-close'),
    bannerContainer: $('#banner-container'),
    bufferIndicator: $('#buffer-indicator'),
    bufferTokens: $('#buffer-tokens'),
    btnNewSession: $('#btn-new-session'),
    btnGovernance: $('#btn-governance'),
    btnMetrics: $('#btn-metrics'),
    btnHealth: $('#btn-health'),
    btnEscalation: $('#btn-escalation'),
    btnPaste: $('#btn-paste'),

    btnFile: $('#btn-file'),

    fileInput: $('#file-input'),

    btnImage: $('#btn-image'),

    imageInput: $('#image-input'),

    imageIndicator: $('#image-indicator'),

    imageName: $('#image-name'),

    imageClear: $('#image-clear'),

    btnScreenshot: $('#btn-screenshot'),
  };

  // ── Auth token ─────────────────────────────────────────────────────────
  // The real token is injected as window.__INTERGEN_TOKEN__ (panel UserScript
  // or server-side index.html injection on an authed doc fetch). If it is
  // absent the client has NO valid credential — fabricating a random one only
  // produces an endless 401 reconnect loop that hides the real cause. Return
  // null and let connect() FAIL LOUD with a "run intergen setup" hint instead.
  function getAuthToken() {
    const token = window.__INTERGEN_TOKEN__ || null;
    // Hand the real token off to same-origin sub-pages (the dashboard is served
    // static, so it gets no server-side injection — it reads this). sessionStorage
    // is per-tab and cleared on close; on the single-user 8089 loopback this only
    // mirrors a token already in this page's JS.
    if (token) {
      try { sessionStorage.setItem('intergen_web_token', token); } catch (e) {}
    }
    return token;
  }

  // ── WebSocket ──────────────────────────────────────────────────────────
  function connect() {
    const token = getAuthToken();
    if (!token) {
      updateConnectionStatus('disconnected');
      showBanner('InterGen is not set up on this account yet — run `intergen '
        + 'setup` in a terminal, then reopen this window.', 'error');
      return;  // no credential → do not open a doomed socket or reconnect-loop
    }
    const url = `${WS_URL}?source_interface=web`;
    state.ws = new WebSocket(url, ['intergen', 'bearer.' + token]);

    state.ws.onopen = () => {
      state.connected = true;
      state.reconnectIdx = 0;
      updateConnectionStatus('connected');
    };

    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleServerMessage(msg);
      } catch (e) {
        console.debug('WS parse error:', e);
      }
    };

    state.ws.onclose = () => {
      state.connected = false;
      updateConnectionStatus('disconnected');
      scheduleReconnect();
    };

    state.ws.onerror = () => {
      updateConnectionStatus('reconnecting');
    };
  }

  function scheduleReconnect() {
    if (state.reconnectTimer) return;
    const delay = RECONNECT_DELAYS[state.reconnectIdx] || 30000;
    state.reconnectIdx = Math.min(state.reconnectIdx + 1, RECONNECT_DELAYS.length - 1);
    updateConnectionStatus('reconnecting');
    state.reconnectTimer = setTimeout(() => {
      state.reconnectTimer = null;
      connect();
    }, delay);
  }

  function send(msg) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(msg));
    }
  }

  // ── Message dispatch ───────────────────────────────────────────────────
  function handleServerMessage(msg) {
    const type = msg.type;
    switch (type) {
      case 'connected':     handleConnected(msg); break;
      case 'stream_start':  handleStreamStart(msg); break;
      case 'tool_ack':      handleToolFiller(msg); break;
      case 'tool_progress': handleToolFiller(msg); break;
      case 'stream_token':  handleStreamToken(msg); break;
      case 'stream_end':    handleStreamEnd(msg); break;
      case 'gate_prompt':   handleGatePrompt(msg); break;
      case 'gate_resolved': handleGateResolved(msg); break;
      case 'tool_executed': handleToolExecuted(msg); break;
      case 'system_status': handleSystemStatus(msg); break;
      case 'health_report': handleHealthReport(msg); break;
      case 'governance_report': handleGovernanceReport(msg); break;
      case 'metrics_report': handleMetricsReport(msg); break;
      case 'session_list':  handleSessionList(msg); break;
      case 'session_switched': handleSessionSwitched(msg); break;
      case 'file_loaded':   handleFileLoaded(msg); break;
      case 'response':      handleResponse(msg); break;
      case 'frontier_response': handleFrontierResponse(msg); break;
      case 'model_changed': handleModelChanged(msg); break;
      case 'error':         handleError(msg); break;
    }
  }

  // ── Handlers ───────────────────────────────────────────────────────────
  function handleConnected(msg) {
    state.clientId = msg.client_id || '';
    state.systemStatus = msg.system_status || {};
    state.governance = msg.system_status?.governance || {};
    // Clear the transient "Connecting to InterGen..." line now that the socket
    // is up. It was previously appended as a permanent transcript line and
    // never removed on the connected frame, so it lingered forever even after
    // a successful connect (exactly the stuck message the operator saw).
    if (state.connectingMsgEl) {
      state.connectingMsgEl.remove();
      state.connectingMsgEl = null;
    }
    // Surface an engine-down state: the daemon + transport can be fully up
    // while no local model/inference server is loaded, in which case a sent
    // message would fail with an opaque error. Tell the user how to fix it.
    const engine = state.systemStatus.engine;
    if (engine && engine.ready === false) {
      if (engine.model_present) {
        // Setup HAS run (the model is on disk) — the inference server just
        // hasn't finished loading the model yet. Normal on first launch / right
        // after login; no user action is needed.
        showBanner("InterGen's model is still starting up — this can take a "
          + 'moment on first launch. It will be ready shortly; no action needed.',
          'warning');
      } else {
        // No model on disk — setup genuinely has not been run.
        showBanner('No local model is set up yet — run `intergen setup` in a '
          + 'terminal to download and start it.', 'error');
      }
    }
    updateHUD();
  }

  function handleStreamStart(msg) {
    clearResponseTimeout();  // server is responding — disarm the failsafe
    state.streaming = true;
    state.streamingContent = '';
    state.turnId = msg.turn_id || '';
    // Hold the thinking pill until the FIRST token actually arrives (the swap
    // happens in handleStreamToken). The model frequently goes straight to a
    // tool call with no leading text, and the tool can take many seconds to
    // run; previously we hid the pill here and revealed the still-empty
    // streaming container, leaving an empty "InterGen" bubble with no activity
    // indicator during that gap (dock arc item 1a, .241 2026-06-10). Keep the
    // pill up and the container hidden until there's real content to show.
    // Reset the pill label to the neutral default; a tool turn replaces it
    // with a hop-1 ack the moment it commits to the tool path (tool_ack).
    if (state.typeTimer) { clearInterval(state.typeTimer); state.typeTimer = null; }
    if (dom.thinkingText) dom.thinkingText.textContent = THINKING_DEFAULT;
    dom.thinking.classList.remove('hidden');
    dom.streamingContainer.classList.add('hidden');
    dom.streamingContent.textContent = '';
    dom.headerMark.style.animation = MARK_THINKING_ANIM;
    scrollToBottom();
  }

  // Perceived-latency fillers (hop-1 ack + hop-2 progress): the thinking
  // indicator SPEAKS the line. Both reuse the pill — make sure it's visible
  // (a hop-2 may arrive after leading text already hid it) and set its text.
  // Any real stream_token hides the pill again (see handleStreamToken).
  function handleToolFiller(msg) {
    if (!msg.text || !dom.thinkingText) return;
    // Type the filler out too — this is the "Let me check on that" line the
    // operator mistook for a timestamp because it appeared instantly. Cancel
    // any in-flight type-out first (a hop-2 nudge replacing a hop-1 ack).
    if (state.typeTimer) { clearInterval(state.typeTimer); state.typeTimer = null; }
    dom.thinking.classList.remove('hidden');
    dom.headerMark.style.animation = MARK_THINKING_ANIM;
    state.typeTimer = typeText(dom.thinkingText, msg.text, {
      onDone: () => { state.typeTimer = null; },
    });
    scrollToBottom();
  }

  function handleStreamToken(msg) {
    const token = msg.token || '';
    // Any real content token ends the filler stage — always hide the pill (it
    // may have been re-shown by a hop-2 nudge after leading text) and cancel an
    // in-flight filler type-out so it can't keep ticking on the hidden pill.
    if (state.typeTimer) { clearInterval(state.typeTimer); state.typeTimer = null; }
    dom.thinking.classList.add('hidden');
    // First token of the stream — reveal the streaming bubble. The container's
    // hidden state is the "first token" flag (set hidden at stream_start).
    if (dom.streamingContainer.classList.contains('hidden')) {
      dom.streamingContainer.classList.remove('hidden');
    }
    state.streamingContent += token;
    dom.streamingContent.textContent = state.streamingContent;
    scrollToBottom();
  }

  function handleStreamEnd(msg) {
    state.streaming = false;
    const full = msg.full_response || state.streamingContent;
    const source = msg.source || '';
    // Terminal for this turn: clear both the streaming bubble and the pill (the
    // latter guarantees no stuck "thinking" if the stream ended without ever
    // emitting a token).
    dom.streamingContainer.classList.add('hidden');
    dom.streamingContent.textContent = '';
    dom.thinking.classList.add('hidden');
    dom.headerMark.style.animation = '';
    addMessage('assistant', full, source);
    if (msg.stats) addStatsRow(msg.stats, msg.confidence);
    if (msg.escalation_offer) addFrontierOffer(msg.escalation_offer);
    state.streamingContent = '';
  }

  // Phone-a-friend OFFER (decision #4): render an "Ask my frontier model" affordance
  // under the local answer. Clicking it sends a frontier_escalate for the LAST user
  // message; the daemon shows a show-before-send consent dialog (the user reviews the
  // exact outbound content) before anything leaves the machine. Clicking NEVER sends
  // on its own — consent is still required at the modal.
  function addFrontierOffer(offerText) {
    if (!state.lastUserMessage) return;
    const el = document.createElement('div');
    el.className = 'message assistant frontier-offer';
    const btn = document.createElement('button');
    btn.className = 'frontier-offer-btn';
    btn.textContent = 'Ask my frontier model';
    btn.addEventListener('click', () => {
      btn.disabled = true;
      sendFrontierEscalate(state.lastUserMessage);
    });
    const note = document.createElement('div');
    note.className = 'content';
    note.textContent = offerText;
    el.appendChild(note);
    el.appendChild(btn);
    dom.messages.appendChild(el);
    scrollToBottom();
  }

  function sendFrontierEscalate(content) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      showBanner('Review the send in the consent dialog…', 'info');
      send({ type: 'frontier_escalate', content });
    } else {
      showBanner('Not connected to InterGen. Check if the daemon is running.', 'error');
    }
  }

  function handleFrontierResponse(msg) {
    if (msg.sent) {
      const src = msg.provider ? 'frontier:' + msg.provider : 'frontier';
      addMessage('assistant', msg.content, src, { typewriter: true });
    } else {
      showBanner(msg.content || 'Nothing was sent to the frontier model.', 'info');
    }
  }

  function handleGatePrompt(msg) {
    state.pendingGate = msg;
    state.streaming = false;
    dom.streamingContainer.classList.add('hidden');
    dom.headerMark.style.animation = '';
    const gateEl = document.createElement('div');
    gateEl.className = 'message assistant gate-card';
    gateEl.innerHTML = buildGateCard(msg);
    dom.messages.appendChild(gateEl);
    scrollToBottom();
  }

  function handleGateResolved(msg) {
    state.pendingGate = null;
    state.streamingContent = '';
    // single-gate review: the user-facing outcome of a deny is now the deterministic honest
    // handoff (streamed as tokens by the server) — advising the real path
    // forward, not a bare "action denied" system line. So nothing extra is
    // rendered here; the resolved event just clears the pending-gate state.
  }

  function handleToolExecuted(msg) {
    const icon = msg.success ? '✓' : '✗';
    const summary = (msg.summary || '').trim();
    const el = document.createElement('div');
    el.className = 'message system tool';
    let html = `<div class="tool-line">${icon} <strong>${escapeHTML(msg.tool_name)}</strong>: ${escapeHTML(summary)}</div>`;
    // "Show full output" expander — only present when the server sent the full
    // payload (i.e. it differs from the summary line). Toggled by a delegated
    // click handler (see below); the full output is escaped into a <pre>.
    if (typeof msg.full_output === 'string' && msg.full_output.length) {
      html += `<button class="tool-toggle" type="button" aria-expanded="false">▸ show full output</button>`;
      html += `<pre class="tool-full" hidden>${escapeHTML(msg.full_output)}</pre>`;
    }
    el.innerHTML = html;
    dom.messages.appendChild(el);
    scrollToBottom();
  }

  // The switch is rendered from the SERVER's reply, never optimistically. The
  // transcript that arrives is what the server actually loaded, so what the
  // user sees and the message_count it reports come from the same load.
  function handleSessionSwitched(msg) {
    stopThinking();
    dom.messages.innerHTML = '';
    const messages = Array.isArray(msg.messages) ? msg.messages : [];
    for (const m of messages) {
      addMessage(m.role || 'assistant', m.content || '', '');
    }
    const label = state.switchingTo || msg.session_id || '';
    state.switchingTo = '';
    if (!messages.length) {
      // An empty session is a real state and says so — distinct from the
      // failure this replaced, where a session WITH history rendered blank.
      addMessage('system', `Switched to session: ${label} (no messages yet)`, '');
    }
    scrollToBottom();
  }

  function handleFileLoaded(msg) {
    addMessage('system',
      `File loaded: ${msg.filename || 'file'} (${msg.chars || 0} chars)`, '');
  }

  function handleSystemStatus(msg) {
    state.systemStatus = msg;
    state.governance = msg.governance || {};
    updateHUD();
  }

  function handleHealthReport(msg) {
    const el = document.createElement('div');
    el.className = 'message assistant';
    el.innerHTML = `<span class="sender">Health Report</span><div class="content">${buildHealthCard(msg)}</div>`;
    dom.messages.appendChild(el);
    scrollToBottom();
  }

  function handleGovernanceReport(msg) {
    const el = document.createElement('div');
    el.className = 'message assistant';
    el.innerHTML = `<span class="sender">Governance</span><div class="content">${buildGovernanceCard(msg)}</div>`;
    dom.messages.appendChild(el);
    scrollToBottom();
  }

  function handleMetricsReport(msg) {
    const el = document.createElement('div');
    el.className = 'message assistant';
    const req = msg.requests || 0;
    const llm = msg.llm_calls || 0;
    const esc = msg.escalations || 0;
    el.innerHTML = `<span class="sender">Metrics</span><div class="content">
      <p>Requests: ${req} | LLM calls: ${llm} | Escalations: ${esc}</p>
    </div>`;
    dom.messages.appendChild(el);
    scrollToBottom();
  }

  function handleSessionList(msg) {
    if (msg.current_session_id) state.sessionId = msg.current_session_id;
    renderSessions(msg.sessions || []);
  }

  function handleResponse(msg) {
    // Fast-path (cache/keyword/semantic/identity/memory) answer — non-streaming.
    // This previously never cleared the thinking indicator, leaving the mark
    // glowing until the next interaction (G3-17 sibling). Clear it here.
    stopThinking();
    if (msg.content) {
      // Type the reply out rather than landing it instantly. The "show full
      // output" expander is attached once typing finishes so it doesn't pop in
      // above the still-typing text (the delegated handler toggles
      // button.nextElementSibling, so the <pre> must follow the button).
      const el = addMessage('assistant', msg.content, msg.source || '', {
        typewriter: true,
        onTyped: () => {
          if (el && typeof msg.full_output === 'string' && msg.full_output.length) {
            const btn = document.createElement('button');
            btn.className = 'tool-toggle';
            btn.type = 'button';
            btn.setAttribute('aria-expanded', 'false');
            btn.textContent = '▸ show full output';
            const pre = document.createElement('pre');
            pre.className = 'tool-full';
            pre.hidden = true;
            pre.textContent = msg.full_output;
            el.appendChild(btn);
            el.appendChild(pre);
          }
        },
      });
    }
  }

  function handleModelChanged(msg) {
    state.modelTier = msg.tier;
    dom.hudModel.textContent = msg.tier;
  }

  function handleError(msg) {
    // Clear the thinking state on any error so the mark doesn't glow forever.
    stopThinking();
    dom.streamingContainer.classList.add('hidden');
    const code = msg.code || 'unknown';
    const message = msg.message || 'An error occurred';
    showBanner(`Error [${code}]: ${message}`, 'error');
  }

  // ── Message rendering ──────────────────────────────────────────────────
  // Type text into an element a few characters per tick so InterGen's replies
  // "type out" instead of landing all at once (operator: an instant fast-path
  // reply read like a stray timestamp — he didn't register it as a reply). The
  // streaming path already animates via real tokens; this is for the instant
  // paths (fast-path responses, frontier replies, the thinking-pill filler).
  // Total duration is capped so long answers don't crawl. textContent is set
  // incrementally (auto-escaped — no HTML injection). Returns the interval id
  // so callers that reuse one target (the pill) can cancel an in-flight run.
  const TYPE_CHAR_MS = 16;   // per-tick cadence
  const TYPE_MAX_MS = 1400;  // cap total type-out for long replies
  function typeText(targetEl, text, opts) {
    opts = opts || {};
    const full = String(text == null ? '' : text);
    targetEl.textContent = '';
    const ticks = Math.max(1, Math.round(TYPE_MAX_MS / TYPE_CHAR_MS));
    const perTick = Math.max(1, Math.ceil(full.length / ticks));
    let i = 0;
    const timer = setInterval(() => {
      i += perTick;
      targetEl.textContent = full.slice(0, i);
      if (opts.scroll !== false) scrollToBottom();
      if (i >= full.length) {
        clearInterval(timer);
        targetEl.textContent = full;
        if (opts.onDone) opts.onDone();
      }
    }, TYPE_CHAR_MS);
    return timer;
  }

  function addMessage(role, content, source, opts) {
    opts = opts || {};
    const el = document.createElement('div');
    el.className = `message ${role}`;
    let senderHTML = '';
    if (role === 'user') senderHTML = '<span class="sender">You</span>';
    else if (role === 'assistant') senderHTML = '<span class="sender">InterGen</span>';
    el.innerHTML = senderHTML + `<div class="content"></div><span class="timestamp">${timeNow()}</span>`;
    const contentEl = el.querySelector('.content');
    dom.messages.appendChild(el);
    // Assistant replies render a safe markdown subset (escape-first); user and
    // system text stays literal so a stray backtick never becomes markup.
    const renderFinal = () => {
      if (role === 'assistant') contentEl.innerHTML = renderMarkdownSafe(content);
      else contentEl.textContent = content;
    };
    if (opts.typewriter) {
      // Type the raw text out, then resolve it into rendered markdown.
      typeText(contentEl, content, { onDone: () => { renderFinal(); if (opts.onTyped) opts.onTyped(); } });
    } else {
      renderFinal();
      if (opts.onTyped) opts.onTyped();
    }
    state.messages.push({ role, content, source, timestamp: timeNow() });
    if (state.messages.length > 200) {
      state.messages.splice(0, 50);
      const keep = dom.messages.children;
      for (let i = 0; i < 50 && i < keep.length; i++) {
        keep[i].remove();
      }
    }
    scrollToBottom();
    return el;
  }

  function addStatsRow(stats, confidence) {
    const el = document.createElement('div');
    el.className = 'message system stats-panel';
    // Every cell is numeric BY CONSTRUCTION: a non-number collapses to 0 (or n/a
    // for the optional confidence), so no daemon/route/model text can reach the
    // assigned innerHTML as live markup. The counters are daemon-computed numbers
    // today; coercing at the sink makes the whole row safe-by-construction rather
    // than safe-because-the-producer-sends-numbers (escape-first posture).
    const num = (v) => (typeof v === 'number') ? v : 0;
    const conf = (typeof confidence === 'number') ? confidence.toFixed(2) : 'n/a';
    el.innerHTML = `
      <span>⏱ ${num(stats.total_ms)}ms</span> |
      <span>tokens: ${num(stats.tokens)}</span> |
      <span>tools: ${num(stats.tool_calls_count)}</span> |
      <span>conf: ${conf}</span>
    `;
    dom.messages.appendChild(el);
  }

  // ── Permission prompt ──────────────────────────────────────────────────
  // This is NOT an error and NOT "blocked" — the action is simply paused for
  // the user's OK (it needs administrator rights). Keep it plain and friendly:
  // what InterGen wants to do, and the choice. No jargon (provenance, gate
  // internals, raw JSON) a normal person shouldn't have to read.
  function buildGateCard(msg) {
    const tcId = escapeHTML(msg.tool_call_id || '');
    const tool = msg.tool_name || '?';
    // the review card: the server now sends a `card` object translated to plain
    // user language (what / command / classification / footer / optional egress
    // reason). Render it as a trust surface — the concrete command shown for
    // transparency, the computed classification in a user sentence (never the raw
    // "privileged_state_changing" label), and the administrator-boundary note up
    // front. v1 buttons are Approve / Deny per ruling 1c. Falls back to the
    // legacy phrasing when an old server sends no card object.
    const card = msg.card;
    let html = `<span class="sender">Permission needed — ${escapeHTML(tool)}</span>`;
    if (card && typeof card === 'object') {
      html += `<div class="content">`;
      html += `<p>InterGen would like to <strong>${escapeHTML(card.what || 'do this')}</strong></p>`;
      if (card.command) {
        html += `<p class="gate-command"><code>${escapeHTML(card.command)}</code></p>`;
      }
      if (card.classification) {
        html += `<p class="gate-classification">${escapeHTML(card.classification)}</p>`;
      }
      // The egress-scan reason, when the outbound content was flagged for review
      // (folded in by the registry) — surfaced, never silently dropped.
      if (card.reason) {
        html += `<p class="gate-reason">${escapeHTML(card.reason)}</p>`;
      }
      if (card.footer) {
        html += `<p class="gate-footer">${escapeHTML(card.footer)}</p>`;
      }
      html += `<div class="gate-actions">
        <button class="gate-btn allow" data-tcid="${tcId}" data-decision="allow">Approve</button>
        <button class="gate-btn deny" data-tcid="${tcId}" data-decision="deny">Deny</button>
      </div></div>`;
      return html;
    }
    // Legacy fallback (older server sent no card): describe from raw action + provenance.
    const provLabel = (msg.provenance || {}).classification || 'user_direct';
    const provClass = { user_direct: 'direct', user_implied: 'implied', ingress_derived: 'ingress' }[provLabel] || 'direct';
    const what = describeGateAction(tool, msg.action || '');
    html += `<span class="provenance-badge ${provClass}">${escapeHTML(provLabel)}</span>`;
    html += `<div class="content"><p>InterGen would like to <strong>${escapeHTML(what)}</strong>. This needs administrator rights — allow it?</p>`;
    html += `<div class="gate-actions">
      <button class="gate-btn allow" data-tcid="${tcId}" data-decision="allow">Allow once</button>
      <button class="gate-btn allow-conv" data-tcid="${tcId}" data-decision="allow_conversation">Allow this session</button>
      <button class="gate-btn deny" data-tcid="${tcId}" data-decision="deny">Not now</button>
    </div></div>`;
    return html;
  }

  // Translate a tool call into a plain-language phrase for the permission prompt.
  function describeGateAction(tool, actionJson) {
    let a = {};
    try { a = JSON.parse(actionJson); } catch (e) { a = {}; }
    const act = (a.action || '').toLowerCase();
    const svc = a.service || a.unit || '';
    const pkg = a.package || '';
    if (tool === 'manage_services' && svc) {
      const verb = { restart: 'restart', stop: 'stop', start: 'start', enable: 'enable', disable: 'disable', reload: 'reload' }[act] || act || 'change';
      return `${verb} the ${svc} service`;
    }
    if (tool === 'manage_packages' && pkg) {
      const verb = { install: 'install', remove: 'remove', uninstall: 'remove', update: 'update', upgrade: 'update' }[act] || act || 'manage';
      return `${verb} the ${pkg} package`;
    }
    if (tool === 'write_file' && a.path) return `save changes to ${a.path}`;
    if (tool === 'run_command' && a.command) return `run: ${String(a.command).slice(0, 80)}`;
    return act ? `${act} (${tool})` : `use ${tool}`;
  }

  // ── Health card builder ────────────────────────────────────────────────
  function buildHealthCard(msg) {
    const layers = msg.layers || [];
    let html = '';
    for (const layer of layers) {
      html += `<p style="color:var(--accent);font-weight:600;margin-top:8px">${escapeHTML(layer.name || '')}</p>`;
      for (const check of layer.checks || []) {
        const dot = { green: '●', yellow: '●', red: '●' }[check.status] || '○';
        const color = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--destructive)' }[check.status] || 'var(--text-ghost)';
        html += `<p style="color:${color};font-size:13px;margin:2px 0">${dot} ${escapeHTML(check.name)}: ${escapeHTML(check.summary || '')}</p>`;
      }
    }
    return html;
  }

  // ── Governance card builder ─────────────────────────────────────────────
  function buildGovernanceCard(msg) {
    const tier = msg.autonomy_tier_name || '?';
    const hashOk = msg.hash_verified;
    const cooldowns = msg.active_cooldowns || 0;
    const cmds = msg.commandments || [];
    const hashColor = hashOk ? 'var(--success)' : 'var(--destructive)';
    const hashText = hashOk ? '✓ VERIFIED' : '✗ UNVERIFIED — TAMPER DETECTED';

    let html = `<p style="font-weight:600;color:var(--accent)">Tier: ${escapeHTML(tier)}</p>`;
    html += `<p style="color:${hashColor}">Hash: ${hashText}</p>`;
    html += `<p style="color:var(--text-dim)">Active cooldowns: ${cooldowns}</p>`;
    html += '<p style="font-weight:600;color:var(--accent);margin-top:8px">The Ten Commandments:</p>';
    for (const c of cmds) {
      const enf = c.enforcement === 'code_enforced' ? '[code]' : '[prompt]';
      html += `<p style="font-size:13px;margin:2px 0;padding-left:8px;border-left:2px solid var(--accent)">${c.num}. ${escapeHTML(c.title)} <span style="color:var(--text-ghost);font-size:10px">${enf}</span></p>`;
    }
    return html;
  }

  // ── HUD ────────────────────────────────────────────────────────────────
  function updateHUD() {
    const ss = state.systemStatus;
    dom.hudModel.textContent = ss.model?.tier || state.modelTier;
    dom.hudTier.textContent = state.governance?.autonomy_tier_name || '--';
    // CTX: real context window from system_status (was hardcoded '--'). Compact
    // form (e.g. 16384 -> "16K"); '--' only when no model/context is up.
    const cs = ss.context_size || 0;
    dom.hudContext.textContent = cs >= 1000 ? Math.round(cs / 1000) + 'K'
                               : (cs > 0 ? String(cs) : '--');
    dom.hudUptime.textContent = fmtUptime(ss.uptime_seconds || 0);
  }

  function updateConnectionStatus(status) {
    dom.connectionStatus.className = 'hud-dot ' + status;
    dom.connectionStatus.title = status.charAt(0).toUpperCase() + status.slice(1);
  }

  // ── Sessions ───────────────────────────────────────────────────────────
  function renderSessions(sessions) {
    dom.sessionList.innerHTML = '';
    for (const s of sessions) {
      const el = document.createElement('div');
      el.className = 'session-item' + (s.session_id === state.sessionId ? ' active' : '');
      el.innerHTML = `${s.is_live ? '<span class="session-badge"></span>' : ''}<div class="session-title">${escapeHTML(s.title || 'Untitled')}</div><div class="session-meta">${escapeHTML(s.updated || '')} · ${escapeHTML(s.category || 'general')}</div>`;
      el.addEventListener('click', () => {
        state.sessionId = s.session_id;
        state.switchingTo = s.title || s.session_id;
        // The pane is repainted when the server's session_switched arrives with
        // the transcript. Clearing and announcing the switch here would assert
        // an outcome the server has not confirmed — and when the transcript
        // never came, that optimistic notice was the whole of what the user saw.
        send({ type: 'switch_session', session_id: s.session_id });
      });
      dom.sessionList.appendChild(el);
    }
  }

  // ── Input ──────────────────────────────────────────────────────────────
  // ── Thinking-state failsafe (G3-17) ──────────────────────────────────────
  function clearResponseTimeout() {
    if (state.pendingResponseTimer) {
      clearTimeout(state.pendingResponseTimer);
      state.pendingResponseTimer = null;
    }
  }

  // Hide the thinking indicator and stop the mark glow. Also disarms the
  // response-timeout. Use for any terminal/first-activity server message.
  function stopThinking() {
    clearResponseTimeout();
    if (state.typeTimer) { clearInterval(state.typeTimer); state.typeTimer = null; }
    dom.headerMark.style.animation = '';
    dom.thinking.classList.add('hidden');
  }

  // Armed when a message is sent. If it fires, no server response ever arrived
  // (half-open socket): clear thinking, surface it honestly, force a reconnect.
  function armResponseTimeout() {
    clearResponseTimeout();
    state.pendingResponseTimer = setTimeout(() => {
      state.pendingResponseTimer = null;
      if (state.streaming) return;  // tokens are flowing — the socket is alive
      dom.headerMark.style.animation = '';
      dom.thinking.classList.add('hidden');
      dom.streamingContainer.classList.add('hidden');
      showBanner("InterGen didn't respond — reconnecting…", 'warning');
      // Force the (likely half-open) socket closed; onclose → scheduleReconnect.
      try { if (state.ws) state.ws.close(); } catch (e) { /* already gone */ }
    }, RESPONSE_TIMEOUT_MS);
  }

  function sendMessage() {
    const text = dom.userInput.value.trim();
    if (!text) return;
    // Record in input history for arrow-up recall (most-recent last; skip an
    // immediate repeat of the previous entry).
    if (state.inputHistory[state.inputHistory.length - 1] !== text) {
      state.inputHistory.push(text);
      if (state.inputHistory.length > 100) state.inputHistory.shift();
    }
    state.historyIndex = null;
    state.historyDraft = '';
    if (text.startsWith('/')) {
      handleSlashCommand(text);
    } else {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        addMessage('user', text);
        state.lastUserMessage = text;
        const msg = { type: 'message', content: text };
        if (state.imageData) {
          msg.image_data = state.imageData;
          msg.image_name = state.imageName;
          state.imageData = null;
          state.imageName = '';
          dom.imageIndicator.classList.add('hidden');
        }
        send(msg);
        // Enter the THINKING state immediately: glow the mark + show the indicator.
        // The local 2B on this CPU can take many seconds before the first token, so
        // this feedback is what tells the user InterGen is working (not stuck).
        dom.headerMark.style.animation = MARK_THINKING_ANIM;
        dom.thinking.classList.remove('hidden');
        armResponseTimeout();
        scrollToBottom();
      } else {
        showBanner('Not connected to InterGen. Check if the daemon is running.', 'error');
      }
    }
    dom.userInput.value = '';
    dom.userInput.style.height = 'auto';
    updateSendButton();
  }

  function handleSlashCommand(text) {
    const parts = text.split(/\s+/);
    const cmd = parts[0].toLowerCase();
    const arg = parts.slice(1).join(' ');

    if (cmd === '/new') {
      dom.messages.innerHTML = '';
      state.messages.length = 0;
      send({ type: 'new_session' });
      addMessage('system', 'New conversation started.', '');
    } else if (cmd === '/clear') {
      dom.messages.innerHTML = '';
      state.messages.length = 0;
    } else if (cmd === '/health') {
      send({ type: 'request_health' });
    } else if (cmd === '/governance') {
      send({ type: 'request_governance' });
    } else if (cmd === '/metrics') {
      send({ type: 'request_metrics' });
    } else if (cmd === '/status') {
      send({ type: 'slash_command', command: '/status' });
    } else if (cmd === '/tier') {
      send({ type: 'slash_command', command: '/tier' });
    } else if (cmd === '/model') {
      const tier = ['small', 'medium', 'large'].includes(arg) ? arg : 'medium';
      send({ type: 'switch_model', tier });
      state.modelTier = tier;
      dom.hudModel.textContent = tier;
    } else if (cmd === '/paste') {
      openModal('Paste Text', '<textarea id="modal-textarea" style="width:100%;min-height:200px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);padding:12px;font-family:var(--font-mono);font-size:13px;resize:vertical"></textarea><button class="primary-btn" style="margin-top:8px" id="modal-paste-btn">Load into Buffer</button>');
      setTimeout(() => {
        const btn = $('#modal-paste-btn');
        if (btn) btn.addEventListener('click', () => {
          const ta = $('#modal-textarea');
          if (ta && ta.value.trim()) {
            addMessage('system', `Buffer loaded (${ta.value.length} chars)`, '');
          }
          closeModal();
        });
      }, 0);
    } else if (cmd === '/file') {
      if (arg) {
        // A path the user named for the ASSISTANT to read — that is an ingress
        // read of this machine, so the server routes it through the registered
        // read_file tool and its gate rather than opening it directly.
        send({ type: 'slash_command', command: '/file', path: arg });
      } else {
        // No path given: offer the real chooser instead of asking the user to
        // type one blind.
        dom.fileInput.click();
      }
    } else if (cmd === '/quit') {
      if (state.ws) state.ws.close();
    } else if (cmd === '/help') {
      addMessage('system', `Slash commands:
/new           Start new conversation
/clear         Clear chat
/model         Switch tier (small/medium/large)
/health        System health report
/governance    Governance dashboard
/metrics       Performance metrics
/status        Daemon status
/tier          Autonomy tier
/paste         Paste text into buffer
/file [path]   Load a file — no path opens a chooser
/screenshot    Capture the screen for analysis
/quit          Disconnect
/help          This help`, 'help');
    } else {
      addMessage('system', `Unknown command: ${cmd}. Type /help for commands.`, '');
    }
    dom.userInput.value = '';
  }

  // ── Modals ─────────────────────────────────────────────────────────────
  function openModal(title, html) {
    dom.modalTitle.textContent = title;
    dom.modalContent.innerHTML = html;
    dom.modalOverlay.classList.remove('hidden');
  }

  function closeModal() {
    dom.modalOverlay.classList.add('hidden');
  }

  // ── Banners ────────────────────────────────────────────────────────────
  function showBanner(message, type) {
    const el = document.createElement('div');
    el.className = `banner ${type || ''}`;
    el.textContent = message;
    dom.bannerContainer.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity 0.3s ease';
      setTimeout(() => el.remove(), 300);
    }, 10000);
  }

  // ── Helpers ────────────────────────────────────────────────────────────
  function escapeHTML(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // Render a SAFE subset of markdown for InterGen's replies. ESCAPE-FIRST per
  // Glasswing: the raw model text is HTML-escaped BEFORE any markup is applied,
  // so the only tags in the output are the ones we add here — the model's own
  // text can never become live HTML. Supports fenced ```code``` blocks (with a
  // copy button), inline `code`, **bold**, and "- " bullet lists. Anything else
  // stays plain text. Returns an HTML string safe to assign via innerHTML.
  function renderMarkdownSafe(raw) {
    const esc = escapeHTML(String(raw == null ? '' : raw));
    const blocks = [];
    const SENT = String.fromCharCode(0xE000); // private-use sentinel: a model can't emit it in prose
    // 1. Pull fenced code blocks out first so the inline rules never touch them.
    let s = esc.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
      const i = blocks.length;
      const body = code.replace(/\n$/, '');
      blocks.push(
        '<pre><button class="copy-btn" type="button" aria-label="Copy code">Copy</button>' +
        '<code>' + body + '</code></pre>'
      );
      return SENT + i + SENT;
    });
    // 2. Inline code, 3. bold (markers survive escaping — only < > & are escaped).
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    // 3.5 Verified-citation links: [text](url) -> anchor, but ONLY for the two
    // allow-listed citation targets — the locally installed wiki page and the
    // canonical wiki host. The string is already HTML-escaped and the scheme+host
    // are pinned here, so an assistant answer cannot smuggle an arbitrary link;
    // any other [text](url) stays literal text exactly as before.
    s = s.replace(
      /\[([^\]\n]+)\]\((https:\/\/wiki\.intergenos\.org\/[^)\s"'<>]*|file:\/\/\/usr\/share\/doc\/intergenos\/wiki\/[^)\s"'<>]*)\)/g,
      (_m, text, url) =>
        '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + text + '</a>'
    );
    // 4. Bullet lists: runs of consecutive "- " lines become a <ul>.
    s = s.replace(/(?:^|\n)((?:- .*(?:\n|$))+)/g, (_m, list) => {
      const items = list.replace(/\s+$/, '').split('\n')
        .map((l) => '<li>' + l.replace(/^- /, '').replace(/\s+$/, '') + '</li>').join('');
      return '\n<ul>' + items + '</ul>';
    });
    // 5. Remaining newlines → <br> (lists/blocks are already block-level).
    s = s.replace(/\n/g, '<br>');
    // 6. Restore the fenced blocks, then tidy stray <br> around block elements.
    s = s.replace(new RegExp(SENT + '(\\d+)' + SENT, 'g'), (_m, i) => blocks[Number(i)]);
    s = s.replace(/<br>\s*(<(?:ul|pre))/g, '$1').replace(/(<\/(?:ul|pre)>)\s*<br>/g, '$1');
    return s;
  }

  function timeNow() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function fmtUptime(s) {
    s = Math.floor(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h > 0) return `${h}h${m}m`;
    if (m > 0) return `${m}m${sec}s`;
    return `${sec}s`;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      const chat = $('#chat');
      if (chat) chat.scrollTop = chat.scrollHeight;
    });
  }

  function updateSendButton() {
    dom.btnSend.disabled = !dom.userInput.value.trim();
  }

  // ── Event listeners ────────────────────────────────────────────────────
  dom.userInput.addEventListener('input', () => {
    dom.userInput.style.height = 'auto';
    dom.userInput.style.height = Math.min(dom.userInput.scrollHeight, 160) + 'px';
    updateSendButton();
  });

  dom.userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
      return;
    }
    // Shell-style prompt recall. ArrowUp walks back through prior prompts when
    // the caret is at the very start of the box (so multi-line editing isn't
    // hijacked); ArrowDown walks forward and restores the in-progress draft at
    // the bottom.
    if (e.key === 'ArrowUp' && dom.userInput.selectionStart === 0 && dom.userInput.selectionEnd === 0) {
      if (state.inputHistory.length === 0) return;
      if (state.historyIndex === null) {
        state.historyDraft = dom.userInput.value;
        state.historyIndex = state.inputHistory.length - 1;
      } else if (state.historyIndex > 0) {
        state.historyIndex--;
      }
      e.preventDefault();
      const v = state.inputHistory[state.historyIndex];
      dom.userInput.value = v;
      dom.userInput.dispatchEvent(new Event('input'));
      requestAnimationFrame(() => { dom.userInput.selectionStart = dom.userInput.selectionEnd = v.length; });
      return;
    }
    if (e.key === 'ArrowDown' && state.historyIndex !== null) {
      e.preventDefault();
      if (state.historyIndex < state.inputHistory.length - 1) {
        state.historyIndex++;
        const v = state.inputHistory[state.historyIndex];
        dom.userInput.value = v;
        requestAnimationFrame(() => { dom.userInput.selectionStart = dom.userInput.selectionEnd = v.length; });
      } else {
        state.historyIndex = null;
        dom.userInput.value = state.historyDraft || '';
      }
      dom.userInput.dispatchEvent(new Event('input'));
      return;
    }
    if (e.key === 'Escape') {
      if (state.pendingGate) {
        send({ type: 'gate_decision', tool_call_id: state.pendingGate.tool_call_id, decision: 'deny' });
        state.pendingGate = null;
      }
    }
  });

  dom.btnSend.addEventListener('click', sendMessage);
  dom.sidebarToggle.addEventListener('click', () => {
    dom.sidebar.classList.toggle('collapsed');
  });

  // ── Sidebar resize ─────────────────────────────────────────────────────
  // The SESSIONS sidebar is width-adjustable (drag the handle on its right
  // edge) in addition to collapsible (the ☰ toggle). Width persists in
  // localStorage; double-clicking the handle resets to the default.
  (function initSidebarResize() {
    const MIN = 180, MAX = 480, DEFAULT = 260, KEY = 'intergen_sidebar_width';
    const resizer = document.getElementById('sidebar-resizer');
    if (!resizer || !dom.sidebar) return;

    const saved = parseInt(localStorage.getItem(KEY), 10);
    if (saved >= MIN && saved <= MAX) dom.sidebar.style.width = saved + 'px';

    let dragging = false, startX = 0, startW = 0;

    resizer.addEventListener('pointerdown', (e) => {
      if (dom.sidebar.classList.contains('collapsed')) return;
      dragging = true;
      startX = e.clientX;
      startW = dom.sidebar.getBoundingClientRect().width;
      dom.sidebar.classList.add('resizing');
      resizer.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    resizer.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const w = Math.max(MIN, Math.min(MAX, startW + (e.clientX - startX)));
      dom.sidebar.style.width = w + 'px';
    });

    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      dom.sidebar.classList.remove('resizing');
      try { resizer.releasePointerCapture(e.pointerId); } catch (_) {}
      localStorage.setItem(KEY, String(Math.round(dom.sidebar.getBoundingClientRect().width)));
    }
    resizer.addEventListener('pointerup', endDrag);
    resizer.addEventListener('pointercancel', endDrag);

    resizer.addEventListener('dblclick', () => {
      dom.sidebar.style.width = DEFAULT + 'px';
      localStorage.setItem(KEY, String(DEFAULT));
    });
  })();
  dom.modalClose.addEventListener('click', closeModal);
  dom.modalOverlay.addEventListener('click', (e) => {
    if (e.target === dom.modalOverlay) closeModal();
  });

  // Gate button delegation
  // Tool-output expander toggle (D-2). Delegated so it covers every
  // tool_executed card without per-card listeners.
  dom.messages.addEventListener('click', (e) => {
    const toggle = e.target.closest('.tool-toggle');
    if (!toggle) return;
    const pre = toggle.nextElementSibling;
    if (!pre || !pre.classList.contains('tool-full')) return;
    const show = pre.hidden;
    pre.hidden = !show;
    toggle.setAttribute('aria-expanded', show ? 'true' : 'false');
    toggle.textContent = (show ? '▾ hide full output' : '▸ show full output');
  });

  dom.messages.addEventListener('click', (e) => {
    const btn = e.target.closest('.gate-btn');
    if (!btn) return;
    const tcId = btn.dataset.tcid;
    const decision = btn.dataset.decision;
    send({ type: 'gate_decision', tool_call_id: tcId, decision });
    state.pendingGate = null;
    // Remove all gate buttons from this card
    const card = btn.closest('.gate-card');
    if (card) {
      const actions = card.querySelector('.gate-actions');
      if (actions) {
        const label = decision === 'deny'
          ? 'Okay — I\'ll leave that alone.'
          : 'Thanks — authenticating…';
        actions.innerHTML = `<span style="color:var(--text-dim)">${label}</span>`;
      }
    }
  });

  // Copy-to-clipboard for rendered code blocks (delegated — covers every
  // assistant code block without per-block listeners).
  dom.messages.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const pre = btn.closest('pre');
    const code = pre && pre.querySelector('code');
    if (!code) return;
    navigator.clipboard.writeText(code.textContent || '').then(() => {
      btn.textContent = 'Copied';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1200);
    }).catch(() => {
      btn.textContent = 'Copy failed';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1200);
    });
  });

  // Header buttons
  dom.btnNewSession.addEventListener('click', () => {
    send({ type: 'new_session' });
    dom.messages.innerHTML = '';
    state.messages.length = 0;
    addMessage('system', 'New conversation started.', '');
  });
  dom.btnGovernance.addEventListener('click', () => send({ type: 'request_governance' }));
  dom.btnMetrics.addEventListener('click', () => send({ type: 'request_metrics' }));
  dom.btnHealth.addEventListener('click', () => send({ type: 'request_health' }));
  // Phone — open the escalation (phone-a-friend) provider config on the
  // dashboard. getAuthToken() has already mirrored the token to sessionStorage
  // so the static dashboard page authenticates; the #providers hash deep-links
  // straight to the Providers tab.
  if (dom.btnEscalation) {
    dom.btnEscalation.addEventListener('click', () => {
      getAuthToken();  // ensure the token is in sessionStorage for the dashboard
      window.location.href = '/dashboard.html#providers';
    });
  }
  dom.btnPaste.addEventListener('click', () => {
    openModal('Paste Text', '<textarea id="modal-textarea" style="width:100%;min-height:200px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);padding:12px;font-family:var(--font-mono);font-size:13px;resize:vertical"></textarea><button class="primary-btn" style="margin-top:8px" id="modal-paste-btn">Load into Buffer</button>');
    setTimeout(() => {
      const btn = $('#modal-paste-btn');
      if (btn) btn.addEventListener('click', () => {
        const ta = $('#modal-textarea');
        if (ta && ta.value.trim()) {
          addMessage('system', `Buffer loaded (${ta.value.length} chars, ~${Math.round(ta.value.length / 4)} tokens)`, '');
          dom.bufferIndicator.classList.remove('hidden');
          dom.bufferTokens.textContent = Math.round(ta.value.length / 4);
        }
        closeModal();
      });
    }, 0);
  });
  // A real chooser, the same pattern the adjacent image button already uses.
  // The browser's own picker is the file surface the user knows; a raw JS
  // prompt() asks a person to type an absolute path from memory, cannot browse,
  // cannot validate, and reveals nothing about what it will do with the answer.
  dom.btnFile.addEventListener('click', () => {
    dom.fileInput.click();
  });

  dom.fileInput.addEventListener('change', () => {
    const file = dom.fileInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      send({
        type: 'slash_command',
        command: '/file',
        filename: file.name,
        content: e.target.result,
      });
      dom.fileInput.value = '';
    };
    reader.onerror = () => {
      addMessage('system', `Could not read ${file.name}.`, '');
      dom.fileInput.value = '';
    };
    reader.readAsText(file);
  });

  // Image attachment
  dom.btnImage.addEventListener('click', () => {
    dom.imageInput.click();
  });

  dom.imageInput.addEventListener('change', () => {
    const file = dom.imageInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      state.imageData = e.target.result;
      state.imageName = file.name;
      dom.imageName.textContent = `${file.name} (${Math.round(file.size/1024)}KB)`;
      dom.imageIndicator.classList.remove('hidden');
      addMessage('system', `Image attached: ${file.name} (${Math.round(file.size/1024)}KB)`, '');
    };
    reader.readAsDataURL(file);
  });

  dom.imageClear.addEventListener('click', () => {
    state.imageData = null;
    state.imageName = '';
    dom.imageIndicator.classList.add('hidden');
    dom.imageInput.value = '';
  });

  // No optimistic "Capturing screenshot..." line: the server acks the request
  // (tool_ack) and reports the outcome (tool_executed), success or failure. A
  // line printed before anything happened claimed a capture that, until this
  // command was registered, never occurred at all.
  dom.btnScreenshot.addEventListener('click', () => {
    send({ type: 'slash_command', command: '/screenshot' });
  });

  // Image lightbox: click on embedded images in messages
  dom.messages.addEventListener('click', (e) => {
    const img = e.target.closest('img.message-image');
    if (!img) return;
    const overlay = document.createElement('div');
    overlay.className = 'lightbox-overlay';
    overlay.innerHTML = `<img src="${img.src}" style="max-width:90vw;max-height:90vh;border-radius:var(--radius-card);box-shadow:0 0 40px rgba(0,153,255,0.2)">`;
    overlay.addEventListener('click', () => overlay.remove());
    document.body.appendChild(overlay);
  });

  // Also use Escape to close lightbox
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const lb = document.querySelector('.lightbox-overlay');
      if (lb) lb.remove();
    }
  });


  // ── Init ───────────────────────────────────────────────────────────────
  // Hold a ref to the transient connecting line so handleConnected can remove
  // it once the socket is up (it used to linger forever as a transcript line).
  state.connectingMsgEl = addMessage('system', 'Connecting to InterGen...', '');
  connect();
})();