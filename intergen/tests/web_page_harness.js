// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenJLU
//
// Runs the shipped web page, intergen/web/app.js, whole, under node, with a
// small stand-in for the document and for the WebSocket. Nothing in the page
// is replaced except a constant the caller names (a timer shortened so a case
// can wait it out), and the run refuses to start if that constant is not
// declared exactly once.
//
// A list of steps drives the page: open the socket, deliver a frame from the
// server, type a line and press Enter, answer the review card on screen,
// press a key, click an element, wait, take a mark. What the page did is
// printed as one JSON object: every frame it sent, every close of its socket
// (with the time since start), every banner it showed, the lines in its
// message pane at each mark and at the end, and anything the page logged —
// the page swallows an exception thrown inside a frame handler and logs it,
// so a stand-in the page cannot use shows up there instead of passing quietly.
//
// Usage: node web_page_harness.js <app.js> <plan.json>
// plan.json: {"constants": {"NAME": value, ...}, "steps": [step, ...]}
// Steps: {"open": true} · {"server": {frame}} · {"close_from_server": true} ·
//        {"type": "text"} (then Enter) ·
//        {"click_gate": "allow" | "deny"} · {"key": "Escape"} ·
//        {"click": "#element-id"} · {"set": {"#element-id": "value"}} ·
//        {"classes": {"#element-id": "names"}} (as the page's markup starts it) ·
//        {"inspect": "#element-id"} (its classes and text, into "inspected") ·
//        {"wait": ms} · {"mark": "label"}

'use strict';

const fs = require('fs');
const vm = require('vm');

const [, , appPath, planPath] = process.argv;
const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
let source = fs.readFileSync(appPath, 'utf8');
for (const [name, value] of Object.entries(plan.constants || {})) {
  const declaration = new RegExp('const ' + name + ' = [^;]+;', 'g');
  const found = source.match(declaration) || [];
  if (found.length !== 1) {
    process.stderr.write(`constant ${name}: ${found.length} declarations in ` +
                         `${appPath}, expected exactly 1\n`);
    process.exit(3);
  }
  source = source.replace(declaration, `const ${name} = ${JSON.stringify(value)};`);
}

const started = Date.now();
const report = { sent: [], closes: [], sockets: 0, banners: [], marks: [],
                 pane: [], logged: [], inspected: [] };

class FakeClassList {
  constructor() { this.names = new Set(); }
  add(...names) { names.forEach((n) => this.names.add(n)); }
  remove(...names) { names.forEach((n) => this.names.delete(n)); }
  contains(name) { return this.names.has(name); }
  toggle(name) {
    if (this.names.has(name)) { this.names.delete(name); return false; }
    this.names.add(name);
    return true;
  }
}

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.parent = null;
    this.classList = new FakeClassList();
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.attributes = {};
    this.inner = {};
    this.ownText = '';
    this.ownHtml = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.files = [];
  }
  set className(value) {
    this.classList = new FakeClassList();
    String(value).split(/\s+/).filter(Boolean).forEach((n) => this.classList.add(n));
  }
  get className() { return [...this.classList.names].join(' '); }
  set textContent(value) { this.ownText = String(value); this.ownHtml = ''; this.dropChildren(); }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(''); }
  set innerHTML(value) { this.ownHtml = String(value); this.ownText = ''; this.dropChildren(); }
  // Text set through textContent reads back as markup with its specials
  // escaped, as a browser serialises a text node: the page's escapeHTML
  // depends on exactly that.
  get innerHTML() {
    if (this.ownHtml || !this.ownText) return this.ownHtml;
    return this.ownText.replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
  }
  dropChildren() {
    this.children.forEach((c) => { c.parent = null; });
    this.children = [];
    this.inner = {};
  }
  appendChild(child) {
    if (child.parent) child.remove();
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parent) return;
    const at = this.parent.children.indexOf(this);
    if (at >= 0) this.parent.children.splice(at, 1);
    this.parent = null;
  }
  // The page asks an element for a part of the markup it has just set
  // (addMessage's '.content', a card's '.gate-actions'); the part is kept here
  // so what the page writes into it can be read back.
  querySelector(selector) {
    if (!this.inner[selector]) this.inner[selector] = new FakeElement('div');
    return this.inner[selector];
  }
  addEventListener(type, listener) {
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  }
  dispatchEvent(event) {
    (this.listeners[event.type] || []).forEach((listener) => listener(event));
    return true;
  }
  closest() { return null; }
  click() { this.dispatchEvent({ type: 'click', target: this, preventDefault() {} }); }
  focus() {}
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  getBoundingClientRect() { return { width: 260, height: 0 }; }
  setPointerCapture() {}
  releasePointerCapture() {}
}

const bySelector = {};
const document = {
  body: new FakeElement('body'),
  querySelector(selector) {
    if (!bySelector[selector]) bySelector[selector] = new FakeElement('div');
    return bySelector[selector];
  },
  querySelectorAll() { return []; },
  getElementById() { return null; },
  createElement(tag) { return new FakeElement(tag); },
  listeners: {},
  addEventListener(type, listener) {
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  },
};

const banners = document.querySelector('#banner-container');
const bannerAppend = banners.appendChild.bind(banners);
banners.appendChild = (child) => {
  report.banners.push({ text: child.textContent, kind: child.className, at: Date.now() - started });
  return bannerAppend(child);
};

const sockets = [];
class FakeWebSocket {
  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.readyState = FakeWebSocket.CONNECTING;
    sockets.push(this);
    report.sockets = sockets.length;
  }
  send(data) {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error('send on a socket that is not open');
    }
    report.sent.push(JSON.parse(data));
  }
  close() {
    report.closes.push({ socket: sockets.indexOf(this), at: Date.now() - started });
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    setTimeout(() => { if (this.onclose) this.onclose({}); }, 0);
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;

function storage() {
  const items = {};
  return {
    getItem: (k) => (k in items ? items[k] : null),
    setItem: (k, v) => { items[k] = String(v); },
    removeItem: (k) => { delete items[k]; },
  };
}

const logged = (level) => (...args) => {
  report.logged.push([level, args.map((a) => String(a && a.stack ? a.stack : a)).join(' ')]);
};

const context = {
  document,
  WebSocket: FakeWebSocket,
  console: { log: logged('log'), debug: logged('debug'), info: logged('info'),
             warn: logged('warn'), error: logged('error') },
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (callback) => setTimeout(callback, 0),
  sessionStorage: storage(),
  localStorage: storage(),
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  Event: class { constructor(type) { this.type = type; } },
  FileReader: class {},
  __INTERGEN_TOKEN__: 'harness-token',
  location: { href: '' },
};
context.window = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: 'app.js' });

function pane() {
  return document.querySelector('#messages').children.map((child) => {
    const content = child.inner['.content'];
    return {
      cls: child.className,
      text: content ? (content.ownText || content.ownHtml) : (child.ownText || child.ownHtml),
    };
  });
}

function socket() { return sockets[sockets.length - 1]; }

function clickGate(decision) {
  const cards = document.querySelector('#messages').children
    .filter((child) => child.classList.contains('gate-card'));
  const card = cards[cards.length - 1];
  if (!card) throw new Error('no review card on the page to answer');
  const id = (card.innerHTML.match(/data-tcid="([^"]*)"/) || [])[1] || '';
  const button = {
    dataset: { tcid: id, decision },
    closest: (selector) => (selector === '.gate-btn' ? button
      : selector === '.gate-card' ? card : null),
  };
  document.querySelector('#messages').dispatchEvent({ type: 'click', target: button });
}

function press(key) {
  document.querySelector('#user-input').dispatchEvent(
    { type: 'keydown', key, shiftKey: false, preventDefault() {} });
}

async function run() {
  for (const step of plan.steps) {
    if ('open' in step) {
      socket().readyState = FakeWebSocket.OPEN;
      if (socket().onopen) socket().onopen({});
    } else if ('server' in step) {
      socket().onmessage({ data: JSON.stringify(step.server) });
    } else if ('close_from_server' in step) {
      socket().readyState = FakeWebSocket.CLOSED;
      if (socket().onclose) socket().onclose({});
    } else if ('type' in step) {
      document.querySelector('#user-input').value = step.type;
      press('Enter');
    } else if ('click_gate' in step) {
      clickGate(step.click_gate);
    } else if ('key' in step) {
      press(step.key);
    } else if ('click' in step) {
      document.querySelector(step.click).click();
    } else if ('set' in step) {
      for (const [selector, value] of Object.entries(step.set)) {
        document.querySelector(selector).value = value;
      }
    } else if ('classes' in step) {
      for (const [selector, names] of Object.entries(step.classes)) {
        document.querySelector(selector).className = names;
      }
    } else if ('inspect' in step) {
      const el = document.querySelector(step.inspect);
      report.inspected.push({ selector: step.inspect, classes: el.className,
                              text: el.textContent, sent: report.sent.length });
    } else if ('wait' in step) {
      await new Promise((resolve) => setTimeout(resolve, step.wait));
    } else if ('mark' in step) {
      report.marks.push({ mark: step.mark, sent: report.sent.length,
                          closes: report.closes.length, pane: pane() });
    } else {
      throw new Error('unknown step ' + JSON.stringify(step));
    }
  }
  report.pane = pane();
  process.stdout.write(JSON.stringify(report));
  process.exit(0);
}

run().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error) + '\n');
  process.exit(2);
});
