// Shared workflow UI: the run card in the transcript, the run strip above the
// composer and the run inspector beside the transcript. Loaded by both /acp
// (templates/acp.html) and the dashboard (templates/index.html), after
// transcript-renderer.js and composer-chrome.js.
//
// One global: `createWorkflowUI(host)`. Each page creates one instance with
// its own element refs and callbacks, so there is no per-page global pair to
// keep in sync (the eight "mirrors index.html — keep in sync" crew and
// sub-agent function pairs this replaces). Everything else lives inside the
// factory's closure.
//
// Incoming dependencies, bare globals from transcript-renderer.js, which must
// load first: `transcriptEl`, `toolRows`, `stuckToBottom`, `elapsedText`.
//
// Data: the `subagents` frame (acp.py `_subagents_frame_payload`) carries
// the rows (`subagents`), the anchor (`toolCallId`), `workflowLive`, and, for
// a workflow, `runs` (name, the step tree from `run_start`, step outputs,
// status) and `questions` (step id -> open step question). A row with an
// empty `workflowId` is a plain sub-agent; its card has no plan and no
// progress bar.
//
// No innerHTML anywhere: every node is built with createElement and
// textContent (tests/acp_page.test.mjs throws on innerHTML). Selectors stay
// single-class, the subset that harness implements.

function createWorkflowUI(host) {
  var NS = 'http://www.w3.org/2000/svg';
  var els = host.els || {};
  // toolCallId (or '_na_N' for a fan-out with no anchor) -> slot.
  // Object.create(null): keyed by a wire-controlled id.
  var slots = Object.create(null);
  var slotOrder = [];
  var noAnchorSeq = 0;
  var noAnchorKey = null;
  var tickTimer = null;
  // Per-run and per-group open state the user chose, so a re-render keeps it.
  var userOpen = Object.create(null);
  // step id -> its reply box, kept across re-renders so typed text survives
  // the frames that rebuild the card around it. One for the card, one for
  // the inspector's step view.
  var askBoxes = Object.create(null);
  var inspOpen = false;
  var inspTab = 'steps';
  var stepSid = null;
  var runsSeen = false;
  var historyReq = 0;
  // Runs and questions already announced, so a re-render says nothing twice.
  var announced = Object.create(null);

  // ---- small DOM helpers ----------------------------------------------------

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null && text !== '') e.textContent = text;
    return e;
  }

  function svg(w, h, draw) {
    var s = document.createElementNS(NS, 'svg');
    s.setAttribute('width', String(w));
    s.setAttribute('height', String(h));
    s.setAttribute('viewBox', '0 0 16 16');
    s.setAttribute('aria-hidden', 'true');
    s.setAttribute('focusable', 'false');
    draw(function (tag, attrs) {
      var n = document.createElementNS(NS, tag);
      for (var k in attrs) n.setAttribute(k, attrs[k]);
      s.appendChild(n);
      return n;
    });
    return s;
  }

  var ICON = {
    workflow: function () {
      return svg(14, 14, function (a) {
        var st = {fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5'};
        a('circle', merge({cx: '3.5', cy: '4', r: '2'}, st));
        a('circle', merge({cx: '12.5', cy: '4', r: '2'}, st));
        a('circle', merge({cx: '8', cy: '12', r: '2'}, st));
        a('path', merge({d: 'M5.5 4h5M4.6 5.7 7 10.2M11.4 5.7 9 10.2'}, st));
      });
    },
    agents: function () {
      return svg(14, 14, function (a) {
        var st = {fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5'};
        a('circle', merge({cx: '5.5', cy: '5.5', r: '2.2'}, st));
        a('circle', merge({cx: '11', cy: '6.5', r: '1.8'}, st));
        a('path', merge({d: 'M1.8 13c.5-2.2 2-3.4 3.7-3.4s3.2 1.2 3.7 3.4M9.6 10.2c1.6-.4 3.6.4 4.4 2.6', 'stroke-linecap': 'round'}, st));
      });
    },
    done: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '7', 'class': 'wf-i-bg'});
        a('path', {d: 'M4.8 8.2 7 10.3l4.2-4.6', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
      });
    },
    waiting: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '7', 'class': 'wf-i-bg'});
        a('rect', {x: '5.6', y: '5', width: '1.6', height: '6', rx: '.6', fill: 'currentColor'});
        a('rect', {x: '8.8', y: '5', width: '1.6', height: '6', rx: '.6', fill: 'currentColor'});
      });
    },
    error: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '7', 'class': 'wf-i-bg'});
        a('path', {d: 'm5.6 5.6 4.8 4.8m0-4.8-4.8 4.8', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round'});
      });
    },
    pending: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '6', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-dasharray': '2.4 2.2'});
      });
    },
    stale: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '7', 'class': 'wf-i-bg'});
        a('path', {d: 'M8 4.6v4M8 11.2v.2', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round'});
      });
    },
    skipped: function () {
      return svg(16, 16, function (a) {
        a('circle', {cx: '8', cy: '8', r: '7', 'class': 'wf-i-bg'});
        a('path', {d: 'M5 8h6', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round'});
      });
    },
    chevron: function () {
      return svg(14, 14, function (a) {
        a('path', {d: 'm4 6 4 4 4-4', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
      });
    },
    panel: function () {
      return svg(14, 14, function (a) {
        a('rect', {x: '2', y: '2.5', width: '12', height: '11', rx: '2', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.4'});
        a('path', {d: 'M10 2.5v11', stroke: 'currentColor', 'stroke-width': '1.4'});
      });
    },
    stop: function () {
      return svg(12, 12, function (a) {
        a('rect', {x: '4', y: '4', width: '8', height: '8', rx: '1.5', fill: 'currentColor'});
      });
    },
    back: function () {
      return svg(14, 14, function (a) {
        a('path', {d: 'M10 4 6 8l4 4', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
      });
    },
    send: function () {
      return svg(14, 14, function (a) {
        a('path', {d: 'M8 12V4M4.5 7.5 8 4l3.5 3.5', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
      });
    },
  };

  function merge(a, b) {
    var o = {};
    for (var k in a) o[k] = a[k];
    for (var j in b) o[j] = b[j];
    return o;
  }

  function stateIcon(state) {
    var wrap = el('span', 'wf-si wf-si-' + state);
    wrap.setAttribute('aria-hidden', 'true');
    if (state === 'working') wrap.appendChild(el('span', 'wf-spin'));
    else wrap.appendChild((ICON[state] || ICON.pending)());
    return wrap;
  }

  // querySelectorAll as an array: a browser returns a NodeList, which has
  // no filter/concat/indexOf.
  function qsa(root, sel) {
    return Array.prototype.slice.call(root.querySelectorAll(sel));
  }

  function clear(node) {
    if (node) node.textContent = '';
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function durText(startedAt, endedAt) {
    var t = elapsedText(startedAt, endedAt);
    return t;
  }

  // ---- the data model -------------------------------------------------------

  // One of working / waiting / done / error / stale for a row PowerAtlas
  // tracks. 'stale' is a finished row closed with no verdict (status 'stale':
  // "no longer reporting", or "stopped" after a Stop), which may still be
  // running, so it never reads as a success.
  function entryState(entry) {
    if (!entry) return 'working';
    if (!entry.done) return entry.status === 'waiting' ? 'waiting' : 'working';
    if (entry.error) return 'error';
    return entry.status === 'stale' ? 'stale' : 'done';
  }

  function entryLabel(entry) {
    if (!entry) return 'agent';
    if (entry.sessionName) return entry.sessionName;
    var t = entry.task || '';
    if (t) return t.length > 30 ? t.slice(0, 30).trim() + '…' : t.trim();
    return 'agent';
  }

  function staleText(entry) {
    var a = entry && entry.action ? entry.action : '';
    if (/stopped/i.test(a)) return 'Stopped';
    return 'No longer reporting. It may still be running.';
  }

  // The activity line of one step.
  function activityText(leaf) {
    var e = leaf.entry;
    switch (leaf.state) {
      case 'working':
        return e && e.action && e.action !== 'running' ? e.action : 'Working…';
      case 'waiting':
        if (leaf.question) return 'Asked you a question';
        return e && e.action && e.action !== 'running' ? e.action : 'Waiting';
      case 'done': return leaf.output || 'Done';
      case 'error': return (e && e.error) || 'Failed';
      case 'stale': return staleText(e);
      case 'skipped': return 'Skipped';
      default: return 'Not started';
    }
  }

  function upperFirst(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  // A slot's rows and run records as cards: one per workflow run (in the
  // order the runs started), then one for the plain sub-agents, if any.
  function buildCards(slot) {
    var byRun = Object.create(null);
    var runOrder = [];
    var plain = [];
    slot.entries.forEach(function (e) {
      if (e.workflowId) {
        if (!byRun[e.workflowId]) { byRun[e.workflowId] = []; runOrder.push(e.workflowId); }
        byRun[e.workflowId].push(e);
      } else {
        plain.push(e);
      }
    });
    var records = Object.create(null);
    (slot.runs || []).forEach(function (r) {
      if (r && typeof r.workflowId === 'string') records[r.workflowId] = r;
    });
    var cards = [];
    runOrder.forEach(function (wf) {
      cards.push(workflowCard(slot, records[wf] || {workflowId: wf}, byRun[wf]));
    });
    if (plain.length) cards.push(agentsCard(slot, plain));
    return cards;
  }

  function leafFor(slot, run, node, entry) {
    var outputs = run.outputs || {};
    var leaf = {
      kind: 'leaf', nodeId: node ? node.nodeId : (entry && entry.nodeId) || '',
      agentName: node ? node.agentName : (entry && entry.sessionName) || '',
      modelId: node ? node.modelId : '', effortLevel: node ? node.effortLevel : '',
      entry: entry || null, output: '', question: '',
    };
    leaf.name = leaf.nodeId || entryLabel(entry);
    if (entry) {
      leaf.state = entryState(entry);
      leaf.question = (slot.questions && slot.questions[entry.sessionId]) || '';
    } else {
      var st = run.status;
      if (st === 'completed') leaf.state = outputs[leaf.nodeId] ? 'done' : 'skipped';
      else if (st === 'failed' || st === 'stopped' || st === 'stale') leaf.state = 'skipped';
      else leaf.state = 'pending';
    }
    if (outputs[leaf.nodeId]) leaf.output = outputs[leaf.nodeId];
    return leaf;
  }

  function workflowCard(slot, run, entries) {
    // The newest row of each node wins (a loop iteration or a re-run).
    var byNode = Object.create(null);
    entries.forEach(function (e) { if (e.nodeId) byNode[e.nodeId] = e; });
    var used = Object.create(null);
    function walk(nodes) {
      var out = [];
      (nodes || []).forEach(function (n) {
        if (n.children && n.children.length) {
          out.push({kind: 'group', nodeId: n.nodeId, type: n.type || 'group', children: walk(n.children)});
        } else {
          var e = byNode[n.nodeId];
          if (e) used[e.sessionId] = true;
          out.push(leafFor(slot, run, n, e));
        }
      });
      return out;
    }
    var items = walk(run.tree);
    if (!items.length) {
      // No plan (a run joined mid-way, or no run_start seen): the rows as
      // they came, grouped by the first segment of their path.
      items = groupByRole(entries.map(function (e) {
        used[e.sessionId] = true;
        return leafFor(slot, run, null, e);
      }));
    }
    // Rows the plan does not name (a loop iteration, say) go at the end.
    entries.forEach(function (e) {
      if (!used[e.sessionId]) items.push(leafFor(slot, run, null, e));
    });
    var leaves = flatten(items);
    var states = leaves.map(function (l) { return l.state; });
    var status = run.status || '';
    if (!status) {
      status = states.indexOf('working') !== -1 || slot.workflowLive ? 'running'
        : states.indexOf('error') !== -1 ? 'failed' : 'completed';
    }
    var live = status === 'running' || status === 'paused';
    var needs = live && states.indexOf('waiting') !== -1;
    var starts = entries.map(function (e) { return e.startedAt; }).filter(isNum);
    var stops = entries.map(function (e) { return e.stoppedAt; }).filter(isNum);
    var startedAt = isNum(run.startedAt) ? run.startedAt : (starts.length ? Math.min.apply(null, starts) : null);
    var endedAt = live ? null : (isNum(run.endedAt) ? run.endedAt : (stops.length ? Math.max.apply(null, stops) : null));
    var result = '';
    for (var i = leaves.length - 1; i >= 0 && !result; i--) result = leaves[i].output || '';
    return {
      kind: 'workflow', id: 'wf:' + run.workflowId, workflowId: run.workflowId,
      name: run.name || 'Workflow', status: status, live: live, needs: needs,
      items: items, leaves: leaves, startedAt: startedAt, endedAt: endedAt,
      result: result, hasPlan: !!(run.tree && run.tree.length),
    };
  }

  function agentsCard(slot, entries) {
    var leaves = entries.map(function (e) {
      var leaf = {kind: 'leaf', nodeId: '', name: entryLabel(e), role: (e.role || '').replace(/^.*\//, ''),
                  agentName: e.sessionName || '', entry: e, state: entryState(e), output: '', question: ''};
      return leaf;
    });
    var states = leaves.map(function (l) { return l.state; });
    var live = states.indexOf('working') !== -1 || states.indexOf('waiting') !== -1;
    var starts = entries.map(function (e) { return e.startedAt; }).filter(isNum);
    var stops = entries.map(function (e) { return e.stoppedAt; }).filter(isNum);
    return {
      kind: 'agents', id: 'agents:' + slot.key, name: plural(entries.length, 'sub-agent', 'sub-agents'),
      status: live ? 'running' : (states.indexOf('error') !== -1 ? 'failed' : 'completed'),
      live: live, needs: states.indexOf('waiting') !== -1, items: groupByRole(leaves), leaves: leaves,
      startedAt: starts.length ? Math.min.apply(null, starts) : null,
      endedAt: live ? null : (stops.length ? Math.max.apply(null, stops) : null),
      result: '', hasPlan: false,
    };
  }

  function isNum(v) { return typeof v === 'number' && v > 0; }

  function groupByRole(leaves) {
    var out = [];
    var groups = Object.create(null);
    leaves.forEach(function (leaf) {
      var role = (leaf.entry && leaf.entry.role) || '';
      var cut = role.indexOf('/');
      if (cut < 0) { out.push(leaf); return; }
      var name = role.slice(0, cut);
      if (!groups[name]) {
        groups[name] = {kind: 'group', nodeId: name, type: '', children: []};
        out.push(groups[name]);
      }
      if (leaf.entry && leaf.entry.workflowId && !leaf.nodeId) leaf.name = role.slice(cut + 1);
      groups[name].children.push(leaf);
    });
    return out;
  }

  function flatten(items) {
    var out = [];
    items.forEach(function (it) {
      if (it.kind === 'group') out = out.concat(flatten(it.children));
      else out.push(it);
    });
    return out;
  }

  var STATUS_PILL = {
    needs: ['Needs you', 'wf-pill-need'], running: ['Running', 'wf-pill-run'],
    paused: ['Paused', 'wf-pill-need'], completed: ['Completed', 'wf-pill-ok'],
    failed: ['Failed', 'wf-pill-fail'], stopped: ['Stopped', 'wf-pill-stop'],
    stale: ['No longer reporting', 'wf-pill-stop'],
  };

  function pillFor(card) {
    var key = card.needs ? 'needs' : card.status;
    var spec = STATUS_PILL[key] || STATUS_PILL.running;
    var pill = el('span', 'wf-pill ' + spec[1]);
    if (key === 'running') pill.appendChild(el('span', 'wf-spin wf-spin-sm'));
    pill.appendChild(el('span', '', spec[0]));
    return pill;
  }

  function pillText(card) {
    return (STATUS_PILL[card.needs ? 'needs' : card.status] || STATUS_PILL.running)[0];
  }

  function doneCount(card) {
    return card.leaves.filter(function (l) { return l.state === 'done'; }).length;
  }

  function metaText(card) {
    var n = card.leaves.length;
    var dur = card.startedAt ? durText(card.startedAt, card.endedAt) : '';
    var count;
    if (card.kind === 'workflow' && card.live && card.hasPlan) {
      count = doneCount(card) + ' of ' + plural(n, 'step', 'steps');
    } else if (card.kind === 'workflow') {
      count = card.status === 'completed' ? plural(n, 'step', 'steps')
        : doneCount(card) + ' of ' + plural(n, 'step', 'steps');
    } else {
      count = card.live ? doneCount(card) + ' of ' + n + ' done' : plural(n, 'agent', 'agents');
    }
    return dur ? count + ' · ' + dur : count;
  }

  // ---- the card -------------------------------------------------------------

  function isOpen(key, dflt) {
    return key in userOpen ? userOpen[key] : dflt;
  }

  // opts.compact: the inspector's list (no stop, no collapse, smaller).
  function renderCard(card, slot, opts) {
    opts = opts || {};
    var root = el('div', 'wf-card' + (card.needs ? ' wf-card-attn' : '') +
      (opts.compact ? ' wf-card-compact' : ''));
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', (card.kind === 'workflow' ? 'Workflow ' : '') +
      card.name + ', ' + pillText(card) + ', ' + metaText(card));
    root.dataset.cardId = card.id;

    var openKey = 'card:' + card.id;
    var expanded = opts.compact ? true
      : isOpen(openKey, !(card.status === 'completed' || card.status === 'stopped' ||
                          (card.kind === 'agents' && !card.live)));

    var head = el('div', 'wf-card-head');
    var glyph = el('span', 'wf-glyph');
    glyph.appendChild(card.kind === 'workflow' ? ICON.workflow() : ICON.agents());
    head.appendChild(glyph);
    var title = el('span', 'wf-card-title');
    title.appendChild(el('span', 'wf-card-name', card.name));
    if (card.kind === 'workflow' && !opts.compact) title.appendChild(el('span', 'wf-card-kind', 'workflow'));
    head.appendChild(title);
    head.appendChild(pillFor(card));
    var meta = el('span', 'wf-card-meta', metaText(card));
    meta.dataset.wfMeta = '1';
    head.appendChild(meta);
    var acts = el('span', 'wf-card-acts');
    if (!opts.compact) {
      if (card.live && card.kind === 'workflow' && typeof host.canStop === 'function' && host.canStop()) {
        var stop = iconButton('wf-icon-btn wf-icon-btn-danger', ICON.stop(), 'Stop the run');
        stop.addEventListener('click', function () { if (typeof host.stopRun === 'function') host.stopRun(); });
        acts.appendChild(stop);
      }
      var open = iconButton('wf-icon-btn', ICON.panel(), 'Open in the run inspector (Ctrl+G)');
      open.addEventListener('click', function () { openInspector('steps'); });
      acts.appendChild(open);
      var tog = iconButton('wf-icon-btn wf-card-toggle' + (expanded ? ' wf-open' : ''), ICON.chevron(),
                           expanded ? 'Hide steps' : 'Show steps');
      tog.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      tog.dataset.focusKey = 'toggle:' + card.id;
      tog.addEventListener('click', function () {
        userOpen[openKey] = !expanded;
        renderAll();
      });
      acts.appendChild(tog);
    }
    head.appendChild(acts);
    root.appendChild(head);

    if (card.hasPlan || card.kind === 'workflow') {
      if (expanded || card.live) root.appendChild(segBar(card.leaves, 'wf-seg'));
    }

    if (expanded) {
      var list = el('div', 'wf-steps');
      list.setAttribute('role', 'list');
      card.items.forEach(function (it) { appendItem(list, it, card, opts, 0); });
      root.appendChild(list);
    }
    if (!opts.compact && !card.live && card.result) {
      var foot = el('div', 'wf-card-foot');
      foot.appendChild(el('span', 'wf-foot-label', 'Result'));
      var res = el('span', 'wf-foot-result', card.result);
      res.title = card.result;
      foot.appendChild(res);
      root.appendChild(foot);
    }
    return root;
  }

  function iconButton(cls, icon, label) {
    var b = el('button', cls);
    b.type = 'button';
    b.setAttribute('aria-label', label);
    b.title = label;
    b.appendChild(icon);
    return b;
  }

  function segBar(leaves, cls) {
    var bar = el('div', cls);
    bar.setAttribute('aria-hidden', 'true');
    leaves.forEach(function (l) { bar.appendChild(el('i', 'wf-seg-' + l.state)); });
    return bar;
  }

  function appendItem(list, it, card, opts, depth) {
    if (it.kind === 'group') {
      list.appendChild(renderGroup(it, card, opts, depth));
      return;
    }
    list.appendChild(renderLeaf(it, card, opts));
    if (it.question && it.entry && it.state === 'waiting') {
      list.appendChild(askBox(it, opts.compact ? 'insp-list' : 'card'));
    }
  }

  function renderGroup(group, card, opts, depth) {
    var leaves = flatten(group.children);
    var states = leaves.map(function (l) { return l.state; });
    var busy = states.indexOf('working') !== -1 || states.indexOf('waiting') !== -1 ||
               states.indexOf('error') !== -1;
    var key = 'group:' + card.id + ':' + group.nodeId;
    var expanded = isOpen(key, busy || card.live || opts.compact);
    var wrap = el('div', 'wf-group');
    wrap.setAttribute('role', 'listitem');
    var head = el('button', 'wf-group-head');
    head.type = 'button';
    head.dataset.focusKey = key;
    head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    var chev = el('span', 'wf-chev' + (expanded ? ' wf-open' : ''));
    chev.appendChild(ICON.chevron());
    head.appendChild(chev);
    head.appendChild(el('span', 'wf-group-name', group.nodeId));
    if (group.type && group.type !== 'group') head.appendChild(el('span', 'wf-tag', group.type));
    var done = states.filter(function (s) { return s === 'done'; }).length;
    var sum = states.indexOf('error') !== -1 ? 'Has a failure'
      : states.indexOf('waiting') !== -1 ? 'Needs you'
      : done === leaves.length ? 'All ' + leaves.length + ' done'
      : done + ' of ' + leaves.length + ' done';
    head.appendChild(el('span', 'wf-group-sum', sum));
    head.setAttribute('aria-label', group.nodeId + (group.type ? ', ' + group.type : '') + ', ' + sum);
    head.addEventListener('click', function () {
      userOpen[key] = !expanded;
      renderAll();
    });
    wrap.appendChild(head);
    if (expanded) {
      var body = el('div', 'wf-group-body');
      body.setAttribute('role', 'list');
      group.children.forEach(function (c) { appendItem(body, c, card, opts, depth + 1); });
      wrap.appendChild(body);
    }
    return wrap;
  }

  function renderLeaf(leaf, card, opts) {
    var e = leaf.entry;
    var interactive = !!(e && e.sessionId);
    // `acp-crew-row` names an openable step or sub-agent row, as it did
    // before this card; the page tests address rows by it.
    var row = el(interactive ? 'button' : 'div', 'wf-step wf-step-' + leaf.state +
      (interactive ? ' acp-crew-row' : '') +
      (interactive && e.sessionId === stepSid && inspOpen ? ' wf-step-sel' : ''));
    row.setAttribute('role', 'listitem');
    if (interactive) {
      row.type = 'button';
      row.dataset.focusKey = 'step:' + (opts.compact ? 'i:' : 'c:') + e.sessionId;
      row.addEventListener('click', function () { showStep(e.sessionId); });
      row.addEventListener('keydown', stepKeys);
    }
    row.appendChild(stateIcon(leaf.state));
    var main = el('span', 'wf-step-main');
    main.appendChild(el('span', 'wf-step-name', leaf.name));
    if (leaf.role && leaf.role !== leaf.name) main.appendChild(el('span', 'wf-step-role', leaf.role));
    var act = el('span', 'wf-step-act', activityText(leaf));
    main.appendChild(act);
    row.appendChild(main);
    var elapsed = el('span', 'wf-step-el');
    if (e && isNum(e.startedAt)) {
      elapsed.textContent = durText(e.startedAt, isNum(e.stoppedAt) ? e.stoppedAt : null);
      if (!isNum(e.stoppedAt) && !e.done) elapsed.dataset.wfTick = String(e.startedAt);
    }
    row.appendChild(elapsed);
    row.title = [leaf.name, leaf.agentName && leaf.agentName !== leaf.name ? leaf.agentName : '',
                 leaf.modelId].filter(Boolean).join(' · ');
    row.setAttribute('aria-label', leaf.name + ', ' + activityText(leaf) +
      (elapsed.textContent ? ', ' + elapsed.textContent : ''));
    return row;
  }

  // Arrow keys move between the step buttons of one list.
  function stepKeys(ev) {
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    var card = ev.currentTarget;
    while (card && !(card.className && String(card.className).indexOf('wf-card') === 0)) card = card.parentNode;
    if (!card) return;
    var rows = qsa(card, '.wf-step').filter(function (r) { return r.tagName === 'BUTTON'; });
    var i = rows.indexOf(ev.currentTarget);
    var next = rows[i + (ev.key === 'ArrowDown' ? 1 : -1)];
    if (next) { ev.preventDefault(); next.focus(); }
  }

  // ---- step questions -------------------------------------------------------

  // The reply box under a waiting step. Kept per step and place, so a
  // re-render re-attaches the same nodes (typed text, focus) instead of new
  // ones. The reply goes to the parent, the path measured on 2026-10-12: the
  // parent passes it on and the paused run resumes in the same step.
  function askBox(leaf, place) {
    var sid = leaf.entry.sessionId;
    var key = place + ':' + sid;
    var box = askBoxes[key];
    if (!box) {
      box = {root: el('div', 'wf-ask')};
      box.root.setAttribute('role', 'group');
      box.from = el('span', 'wf-ask-from');
      box.q = el('span', 'wf-ask-q');
      box.row = el('div', 'wf-ask-row');
      box.input = el('textarea', 'wf-ask-input');
      box.input.rows = 1;
      box.send = el('button', 'wf-btn wf-btn-amber', 'Reply');
      box.send.type = 'button';
      box.hint = el('span', 'wf-ask-hint');
      box.row.appendChild(box.input);
      box.row.appendChild(box.send);
      box.root.appendChild(box.from);
      box.root.appendChild(box.q);
      box.root.appendChild(box.row);
      box.root.appendChild(box.hint);
      var submit = function () {
        var text = box.input.value.trim();
        if (!text) return;
        var ok = typeof host.sendReply === 'function'
          ? host.sendReply('Answer to the question from workflow step "' + box.name + '": ' + text)
          : false;
        if (ok === true) {
          box.input.value = '';
          setHint(box, 'Sent to the agent. It passes your answer on and the run resumes this step.', '');
        } else if (ok === 'busy') {
          setHint(box, 'The agent is still answering. Reply again when its turn ends.', 'wf-ask-hint-warn');
        } else {
          setHint(box, 'Not sent. Check the connection and try again.', 'wf-ask-hint-warn');
        }
      };
      box.send.addEventListener('click', submit);
      box.input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); submit(); }
      });
      askBoxes[key] = box;
    }
    box.name = leaf.name;
    box.from.textContent = leaf.name + ' is asking';
    box.q.textContent = leaf.question;
    box.root.setAttribute('aria-label', 'Question from ' + leaf.name);
    box.input.setAttribute('aria-label', 'Your answer to ' + leaf.name);
    box.input.setAttribute('placeholder', 'Type your answer');
    if (!box.hint.textContent) setHint(box, 'Your reply goes to the agent, which passes it on to this step.', '');
    return box.root;
  }

  function setHint(box, text, cls) {
    box.hint.textContent = text;
    box.hint.className = 'wf-ask-hint' + (cls ? ' ' + cls : '');
  }

  // ---- slots in the transcript ----------------------------------------------

  // The row a slot is anchored after: the spawner's tool row, walked up to a
  // direct child of the transcript (it may sit inside a tool group).
  function anchorRow(key) {
    var anchor = key ? toolRows['t:' + key] : null;
    var row = anchor && anchor.body ? anchor.body.parentNode : null;
    while (row && row.parentNode && row.parentNode !== transcriptEl) row = row.parentNode;
    return row && row.parentNode === transcriptEl ? row : null;
  }

  function setCrew(entries, toolCallId, workflowLive, runs, questions) {
    entries = Array.isArray(entries) ? entries : [];
    var key = (toolCallId && toolCallId !== '') ? toolCallId : null;
    if (!entries.length) {
      if (key && slots[key]) removeSlot(key);
      else if (!key && noAnchorKey && slots[noAnchorKey]) removeSlot(noAnchorKey);
      renderChrome();
      return;
    }
    var useKey;
    if (key) {
      useKey = key;
    } else if (noAnchorKey && slots[noAnchorKey]) {
      useKey = noAnchorKey;
    } else {
      useKey = '_na_' + (++noAnchorSeq);
      noAnchorKey = useKey;
    }
    var s = slots[useKey];
    if (!s) {
      s = slots[useKey] = {key: useKey, entries: [], runs: [], questions: {}, el: null};
      slotOrder.push(useKey);
    }
    s.entries = entries;
    s.runs = Array.isArray(runs) ? runs : [];
    s.questions = questions && typeof questions === 'object' ? questions : {};
    s.workflowLive = workflowLive === true;
    runsSeen = true;
    if (!s.el || !s.el.parentNode) {
      var stick = stuckToBottom();
      s.el = el('div', 'acp-crew-panel wf-slot');
      var row = anchorRow(key);
      if (row) row.parentNode.insertBefore(s.el, row.nextSibling);
      else transcriptEl.appendChild(s.el);
      if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
    }
    renderAll();
  }

  function removeSlot(key) {
    var s = slots[key];
    if (!s) return;
    if (s.el && s.el.parentNode) s.el.remove();
    s.el = null;
    delete slots[key];
    slotOrder = slotOrder.filter(function (k) { return k !== key; });
    if (key === noAnchorKey) noAnchorKey = null;
  }

  function allCards() {
    var out = [];
    slotOrder.forEach(function (k) {
      var s = slots[k];
      if (!s) return;
      buildCards(s).forEach(function (c) { out.push({card: c, slot: s}); });
    });
    return out;
  }

  // Rebuild every card, the strip and the inspector. Focus is kept by key.
  function renderAll() {
    var active = document.activeElement;
    var focusKey = active && active.dataset ? active.dataset.focusKey || null : null;
    var focusedAsk = null;
    for (var k in askBoxes) if (askBoxes[k].input === active) focusedAsk = askBoxes[k].input;
    slotOrder.forEach(function (key) {
      var s = slots[key];
      if (!s || !s.el) return;
      var stick = stuckToBottom();
      clear(s.el);
      buildCards(s).forEach(function (c) {
        s.el.appendChild(renderCard(c, s, {}));
        announce(c);
      });
      if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
    });
    renderChrome();
    if (focusedAsk) focusedAsk.focus();
    else if (focusKey) refocus(focusKey);
    syncTicker();
  }

  function refocus(key) {
    var roots = [];
    slotOrder.forEach(function (k) { if (slots[k] && slots[k].el) roots.push(slots[k].el); });
    if (els.insp) roots.push(els.insp);
    for (var i = 0; i < roots.length; i++) {
      var found = qsa(roots[i], '.wf-step').concat(qsa(roots[i], '.wf-group-head'),
                                                   qsa(roots[i], '.wf-card-toggle'));
      for (var j = 0; j < found.length; j++) {
        if (found[j].dataset && found[j].dataset.focusKey === key) { found[j].focus(); return; }
      }
    }
  }

  // Only the elapsed texts change between frames, once a second.
  function syncTicker() {
    var live = allCards().some(function (x) { return x.card.live; });
    if (live && tickTimer === null) {
      tickTimer = setInterval(tick, 1000);
    } else if (!live && tickTimer !== null) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  }

  function tick() {
    var roots = [];
    slotOrder.forEach(function (k) { if (slots[k] && slots[k].el) roots.push(slots[k].el); });
    if (els.insp && inspOpen) roots.push(els.insp);
    if (els.strip) roots.push(els.strip);
    roots.forEach(function (r) {
      qsa(r, '.wf-step-el').forEach(function (n) {
        if (n.dataset.wfTick) n.textContent = durText(Number(n.dataset.wfTick), null);
      });
    });
    // The card meta and the step view's facts carry a total too.
    var cards = allCards();
    slotOrder.forEach(function (k) {
      var s = slots[k];
      if (!s || !s.el) return;
      qsa(s.el, '.wf-card-meta').forEach(function (m, i) {
        var mine = cards.filter(function (x) { return x.slot === s; })[i];
        if (mine) m.textContent = metaText(mine.card);
      });
    });
    if (stepSid) renderStepHead();
    if (!cards.some(function (x) { return x.card.live; })) syncTicker();
  }

  // Screen reader notes: a run's end once, and each question once.
  function announce(card) {
    if (typeof host.announce !== 'function') return;
    var endKey = card.id + ':' + card.status;
    if (!card.live && card.status !== 'running' && !announced[endKey] && announced[card.id + ':seen']) {
      announced[endKey] = true;
      host.announce(card.name + ' ' + pillText(card).toLowerCase() + '.', false);
    }
    announced[card.id + ':seen'] = true;
    card.leaves.forEach(function (l) {
      if (l.question && l.entry && !announced['q:' + l.entry.sessionId + ':' + l.question]) {
        announced['q:' + l.entry.sessionId + ':' + l.question] = true;
        host.announce(l.name + ' is asking: ' + l.question, true);
      }
    });
  }

  // ---- the strip, the Runs button and the inspector -------------------------

  function liveCard() {
    var list = allCards().filter(function (x) { return x.card.live && x.card.kind === 'workflow'; });
    if (!list.length) list = allCards().filter(function (x) { return x.card.live; });
    return list.length ? list[list.length - 1].card : null;
  }

  function waitingCount() {
    var n = 0;
    allCards().forEach(function (x) {
      x.card.leaves.forEach(function (l) { if (l.state === 'waiting' && x.card.live) n++; });
    });
    return n;
  }

  function renderChrome() {
    renderStrip();
    renderRunsButton();
    if (inspOpen) renderInspector();
  }

  function renderStrip() {
    var strip = els.strip;
    if (!strip) return;
    var card = liveCard();
    clear(strip);
    if (!card) { strip.hidden = true; return; }
    strip.hidden = false;
    var btn = el('button', 'wf-strip-btn');
    btn.type = 'button';
    btn.appendChild(card.needs ? stateIcon('waiting') : stateIcon('working'));
    btn.appendChild(el('span', 'wf-strip-name', card.name));
    if (card.kind === 'workflow' && card.leaves.length) btn.appendChild(segBar(card.leaves, 'wf-strip-seg'));
    var count = el('span', 'wf-strip-count', card.kind === 'workflow'
      ? doneCount(card) + ' of ' + card.leaves.length : doneCount(card) + ' of ' + card.leaves.length + ' done');
    btn.appendChild(count);
    var waiting = waitingCount();
    if (waiting) btn.appendChild(el('span', 'wf-pill wf-pill-need', waiting + ' needs you'));
    var openHint = el('span', 'wf-strip-open');
    openHint.appendChild(el('span', '', inspOpen ? 'Hide run' : 'Open run'));
    openHint.appendChild(el('kbd', 'wf-kbd', 'Ctrl G'));
    btn.appendChild(openHint);
    btn.setAttribute('aria-label', card.name + ', ' + pillText(card) + ', ' + metaText(card) +
      (waiting ? ', ' + waiting + ' needs you' : '') + '. ' + (inspOpen ? 'Hide' : 'Open') + ' the run inspector');
    btn.addEventListener('click', function () { toggleInspector(); });
    strip.appendChild(btn);
  }

  function renderRunsButton() {
    var b = els.runsBtn;
    if (!b) return;
    b.hidden = !runsSeen;
    b.setAttribute('aria-expanded', inspOpen ? 'true' : 'false');
    if (b.classList) b.classList.toggle('wf-runs-btn-on', inspOpen);
    var badge = els.runsBadge;
    if (badge) {
      var n = waitingCount();
      badge.textContent = n ? String(n) : '';
      badge.hidden = !n;
    }
  }

  function openInspector(tab) {
    if (!els.insp) return;
    inspOpen = true;
    if (tab) inspTab = tab;
    els.insp.hidden = false;
    if (els.page && els.page.classList) els.page.classList.add('wf-insp-open');
    renderAll();
    if (inspTab === 'history') loadHistory();
  }

  function closeInspector() {
    if (!inspOpen && !stepSid) {
      if (els.insp) els.insp.hidden = true;
      if (els.stepView) els.stepView.hidden = true;
      return;
    }
    if (stepSid) {
      stepSid = null;
      if (typeof host.closeStep === 'function') host.closeStep();
    }
    inspOpen = false;
    if (els.insp) els.insp.hidden = true;
    if (els.stepView) els.stepView.hidden = true;
    if (els.page && els.page.classList) els.page.classList.remove('wf-insp-open');
    renderAll();
  }

  function toggleInspector() {
    if (inspOpen) closeInspector();
    else openInspector();
  }

  function setTab(tab) {
    inspTab = tab;
    if (tab === 'history') {
      if (stepSid) backToSteps();
      loadHistory();
    }
    renderInspector();
  }

  function renderInspector() {
    if (!els.insp) return;
    var live = liveCard();
    var cards = allCards();
    var latest = live || (cards.length ? cards[cards.length - 1].card : null);
    if (els.inspTitle) els.inspTitle.textContent = latest ? latest.name : 'Runs';
    if (els.inspPill) {
      clear(els.inspPill);
      if (latest) els.inspPill.appendChild(pillFor(latest));
    }
    if (els.tabSteps) {
      els.tabSteps.setAttribute('aria-selected', inspTab === 'steps' ? 'true' : 'false');
      els.tabSteps.className = 'wf-tab' + (inspTab === 'steps' ? ' wf-tab-on' : '');
    }
    if (els.tabHistory) {
      els.tabHistory.setAttribute('aria-selected', inspTab === 'history' ? 'true' : 'false');
      els.tabHistory.className = 'wf-tab' + (inspTab === 'history' ? ' wf-tab-on' : '');
    }
    var showStepView = inspTab === 'steps' && !!stepSid;
    if (els.stepsView) els.stepsView.hidden = inspTab !== 'steps' || showStepView;
    if (els.historyView) els.historyView.hidden = inspTab !== 'history';
    if (els.stepView) els.stepView.hidden = !showStepView;
    if (inspTab === 'steps' && !stepSid && els.stepsView) {
      clear(els.stepsView);
      if (!cards.length) {
        els.stepsView.appendChild(el('div', 'wf-empty', 'No run in this conversation yet. Runs started here appear in this list.'));
      }
      cards.slice().reverse().forEach(function (x) {
        els.stepsView.appendChild(renderCard(x.card, x.slot, {compact: true}));
      });
    }
    if (showStepView) renderStepHead();
  }

  // ---- the step view (the page's sub-agent panel, inside the inspector) -----

  function findLeaf(sid) {
    var cards = allCards();
    for (var i = 0; i < cards.length; i++) {
      var leaves = cards[i].card.leaves;
      for (var j = 0; j < leaves.length; j++) {
        if (leaves[j].entry && leaves[j].entry.sessionId === sid) {
          return {leaf: leaves[j], card: cards[i].card, group: groupOf(cards[i].card.items, leaves[j])};
        }
      }
    }
    return null;
  }

  function groupOf(items, leaf) {
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (it.kind === 'group') {
        if (flatten(it.children).indexOf(leaf) !== -1) return it.nodeId;
      }
    }
    return '';
  }

  function entryFor(sid) {
    var found = findLeaf(sid);
    return found ? found.leaf.entry : null;
  }

  // A step can take a direct message only while its run is live and the step
  // has not finished.
  function isLiveStep(sid) {
    var found = findLeaf(sid);
    return !!(found && found.card.kind === 'workflow' && found.card.live &&
              (found.leaf.state === 'working' || found.leaf.state === 'waiting'));
  }

  function showStep(sid) {
    if (!sid) return;
    stepSid = sid;
    inspTab = 'steps';
    if (!inspOpen) {
      inspOpen = true;
      if (els.insp) els.insp.hidden = false;
      if (els.page && els.page.classList) els.page.classList.add('wf-insp-open');
    }
    if (typeof host.openStep === 'function') host.openStep(sid);
    renderAll();
    renderInspector();
    if (els.stepBack && els.stepBack.focus) els.stepBack.focus();
  }

  function backToSteps() {
    if (!stepSid) return;
    var was = stepSid;
    stepSid = null;
    if (typeof host.closeStep === 'function') host.closeStep();
    renderAll();
    refocus('step:i:' + was);
  }

  function renderStepHead() {
    if (!stepSid) return;
    var found = findLeaf(stepSid);
    var leaf = found ? found.leaf : null;
    var e = leaf ? leaf.entry : null;
    if (els.stepCrumb) {
      clear(els.stepCrumb);
      if (found && found.group) {
        els.stepCrumb.appendChild(el('span', 'wf-crumb-part', found.group));
        els.stepCrumb.appendChild(el('span', 'wf-crumb-sep', '/'));
      }
      els.stepCrumb.appendChild(el('b', 'wf-crumb-name', leaf ? leaf.name : stepSid));
    }
    if (els.stepPill) {
      clear(els.stepPill);
      if (leaf) {
        var spec = {working: ['Working', 'wf-pill-run'], waiting: ['Waiting on you', 'wf-pill-need'],
                    done: ['Done', 'wf-pill-ok'], error: ['Failed', 'wf-pill-fail'],
                    stale: ['No longer reporting', 'wf-pill-stop']}[leaf.state] || ['', ''];
        if (spec[0]) els.stepPill.appendChild(el('span', 'wf-pill ' + spec[1], spec[0]));
      }
    }
    if (els.stepFacts) {
      clear(els.stepFacts);
      var facts = [];
      if (leaf) {
        if (leaf.agentName && leaf.agentName !== leaf.name) facts.push(leaf.agentName);
        if (leaf.modelId) facts.push(leaf.modelId);
        if (leaf.effortLevel) facts.push('effort ' + leaf.effortLevel);
        if (found.card.kind === 'workflow') facts.push(found.card.name);
      }
      facts.forEach(function (f) { els.stepFacts.appendChild(el('span', 'wf-fact', f)); });
      if (e && isNum(e.startedAt)) {
        els.stepFacts.appendChild(el('span', 'wf-fact wf-fact-time',
          durText(e.startedAt, isNum(e.stoppedAt) ? e.stoppedAt : null)));
      }
      els.stepFacts.hidden = !els.stepFacts.childNodes.length;
    }
    if (els.stepAsk) {
      clear(els.stepAsk);
      if (leaf && leaf.question && leaf.state === 'waiting') els.stepAsk.appendChild(askBox(leaf, 'insp'));
      els.stepAsk.hidden = !els.stepAsk.childNodes.length;
    }
    renderStepComposer(leaf);
  }

  function renderStepComposer(leaf) {
    var box = els.stepComposer;
    if (!box) return;
    var live = !!(leaf && isLiveStep(stepSid));
    box.hidden = false;
    if (els.stepInput) {
      els.stepInput.hidden = !live;
      els.stepInput.disabled = !live;
      if (leaf) els.stepInput.setAttribute('placeholder', 'Message ' + leaf.name + ' directly');
    }
    if (els.stepSend) { els.stepSend.hidden = !live; els.stepSend.disabled = !live; }
    if (els.stepNote) {
      var note = live ? 'Goes to this step’s own session, not to the agent above it.'
        : leaf && leaf.entry && leaf.entry.workflowId
          ? 'This step finished. Its conversation is read-only.'
          : 'Sub-agent conversations are read-only.';
      if (!els.stepNote.dataset.error) els.stepNote.textContent = note;
    }
  }

  function sendStepMessage() {
    var input = els.stepInput;
    if (!input || !stepSid) return;
    var text = input.value.trim();
    if (!text || !isLiveStep(stepSid)) return;
    var ok = typeof host.sendStepPrompt === 'function' && host.sendStepPrompt(text, stepSid);
    if (ok !== false) {
      input.value = '';
      if (els.stepNote) els.stepNote.dataset.error = '';
      renderStepComposer((findLeaf(stepSid) || {}).leaf);
    } else if (els.stepNote) {
      els.stepNote.dataset.error = '1';
      els.stepNote.textContent = 'Not sent. Check the connection and try again.';
    }
  }

  // ---- history (past runs, from the session's file) -------------------------

  function loadHistory() {
    var view = els.historyView;
    if (!view) return;
    var sid = typeof host.getSessionId === 'function' ? host.getSessionId() : null;
    clear(view);
    if (!sid) {
      view.appendChild(el('div', 'wf-empty', 'Open a session to see its runs.'));
      return;
    }
    var mine = ++historyReq;
    var loading = el('div', 'wf-empty', 'Loading runs…');
    view.appendChild(loading);
    if (typeof fetch !== 'function') return;
    fetch('/api/workflow-history?sid=' + encodeURIComponent(sid))
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (mine !== historyReq) return;
        renderHistory(Array.isArray(data && data.runs) ? data.runs : []);
      })
      .catch(function () {
        if (mine !== historyReq) return;
        clear(view);
        view.appendChild(el('div', 'wf-empty wf-empty-warn', 'Could not read this session’s runs. Try again in a moment.'));
      });
  }

  var HIST_STATE = {completed: 'done', failed: 'error', error: 'error', running: 'working',
                    in_progress: 'working', cancelled: 'skipped', stopped: 'skipped'};

  function renderHistory(runs) {
    var view = els.historyView;
    clear(view);
    if (els.historyCount) els.historyCount.textContent = runs.length ? String(runs.length) : '';
    if (!runs.length) {
      view.appendChild(el('div', 'wf-empty', 'No runs in this session yet.'));
      return;
    }
    var list = el('div', 'wf-hist');
    list.setAttribute('role', 'list');
    runs.slice().reverse().forEach(function (run, i) {
      var steps = Array.isArray(run.steps) ? run.steps : [];
      var key = 'hist:' + (run.workflowId || i);
      var expanded = isOpen(key, i === 0);
      var item = el('div', 'wf-hist-item');
      item.setAttribute('role', 'listitem');
      var head = el('button', 'wf-hist-head');
      head.type = 'button';
      head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      head.appendChild(stateIcon(HIST_STATE[String(run.status || '').toLowerCase()] || 'done'));
      var txt = el('span', 'wf-hist-text');
      txt.appendChild(el('b', '', run.name || run.workflowId || ('Run ' + (runs.length - i))));
      var parts = [upperFirst(String(run.status || 'completed').replace(/_/g, ' ')), plural(steps.length, 'step', 'steps')];
      var st = toSecs(run.startedAt), en = toSecs(run.endedAt);
      if (st && en) parts.push(durText(st, en));
      txt.appendChild(el('span', '', parts.join(' · ')));
      head.appendChild(txt);
      head.appendChild(el('span', 'wf-hist-when', st ? clock(st) : ''));
      head.addEventListener('click', function () {
        userOpen[key] = !expanded;
        renderHistory(runs);
      });
      item.appendChild(head);
      if (expanded) {
        var body = el('div', 'wf-hist-steps');
        steps.forEach(function (step) {
          var state = HIST_STATE[String(step.status || '').toLowerCase()] || 'done';
          var row = el('div', 'wf-step wf-step-' + state);
          row.appendChild(stateIcon(state));
          var main = el('span', 'wf-step-main');
          main.appendChild(el('span', 'wf-step-name', step.label || step.nodeId || step.agentName || 'step'));
          var out = typeof step.capturedOutput === 'string' ? step.capturedOutput : '';
          main.appendChild(el('span', 'wf-step-act', out || upperFirst(String(step.status || ''))));
          row.appendChild(main);
          body.appendChild(row);
        });
        item.appendChild(body);
      }
      list.appendChild(item);
    });
    view.appendChild(list);
    view.appendChild(el('div', 'wf-hist-note', 'Read from the session’s file on disk, so past runs survive a PowerAtlas restart.'));
  }

  function toSecs(v) {
    if (typeof v === 'number') return v > 1e12 ? v / 1000 : v;
    if (typeof v === 'string' && v) {
      var t = Date.parse(v);
      return isNaN(t) ? null : t / 1000;
    }
    return null;
  }

  function clock(secs) {
    var d = new Date(secs * 1000);
    var hh = String(d.getHours()); if (hh.length < 2) hh = '0' + hh;
    var mm = String(d.getMinutes()); if (mm.length < 2) mm = '0' + mm;
    return hh + ':' + mm;
  }

  // ---- wiring ----------------------------------------------------------------

  if (els.inspClose) els.inspClose.addEventListener('click', closeInspector);
  if (els.runsBtn) els.runsBtn.addEventListener('click', toggleInspector);
  if (els.tabSteps) els.tabSteps.addEventListener('click', function () { setTab('steps'); });
  if (els.tabHistory) els.tabHistory.addEventListener('click', function () { setTab('history'); });
  if (els.stepBack) els.stepBack.addEventListener('click', backToSteps);
  if (els.stepSend) els.stepSend.addEventListener('click', sendStepMessage);
  if (els.stepInput) {
    els.stepInput.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendStepMessage(); }
    });
  }

  // Ctrl+G toggles the inspector (the kiro-cli TUI's own key for its workflow
  // monitor); Esc inside the inspector steps back, then closes it.
  function onKeyDown(ev) {
    if (ev.key === 'g' || ev.key === 'G') {
      if (ev.ctrlKey && !ev.altKey && !ev.metaKey && runsSeen &&
          (typeof host.isActive !== 'function' || host.isActive())) {
        ev.preventDefault();
        if (ev.stopPropagation) ev.stopPropagation();
        toggleInspector();
        return true;
      }
      return false;
    }
    if (ev.key === 'Escape' && host.handleEscape !== false && inspOpen && els.insp &&
        els.insp.contains(document.activeElement)) {
      ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
      stepBackOrClose();
      if (els.runsBtn && els.runsBtn.focus && !inspOpen) els.runsBtn.focus();
      return true;
    }
    return false;
  }

  // One Escape: back from a step to the steps, then close the inspector.
  function stepBackOrClose() {
    if (stepSid) backToSteps();
    else closeInspector();
  }

  function reset() {
    slotOrder.slice().forEach(removeSlot);
    slots = Object.create(null);
    slotOrder = [];
    noAnchorSeq = 0;
    noAnchorKey = null;
    askBoxes = Object.create(null);
    announced = Object.create(null);
    runsSeen = false;
    historyReq++;
    if (els.historyView) clear(els.historyView);
    if (els.historyCount) els.historyCount.textContent = '';
    inspTab = 'steps';
    closeInspector();
    if (tickTimer !== null) { clearInterval(tickTimer); tickTimer = null; }
    renderChrome();
  }

  return {
    setCrew: setCrew,
    reset: reset,
    entryFor: entryFor,
    entryState: entryState,
    entryLabel: entryLabel,
    isLiveStep: isLiveStep,
    showStep: showStep,
    backToSteps: backToSteps,
    openInspector: openInspector,
    closeInspector: closeInspector,
    toggleInspector: toggleInspector,
    stepBackOrClose: stepBackOrClose,
    renderStepHead: renderStepHead,
    onKeyDown: onKeyDown,
    isOpen: function () { return inspOpen; },
    stepSid: function () { return stepSid; },
    hasSlots: function () { return slotOrder.length > 0; },
  };
}
