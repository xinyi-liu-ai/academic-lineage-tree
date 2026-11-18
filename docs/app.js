/* Academic Lineage Tree — static front end (D3 v7). */
(() => {
  'use strict';

  const AREAS = {
    ai: ['AI', { ai: 'Artificial intelligence', vision: 'Computer vision', ml: 'Machine learning', nlp: 'Natural language processing', ir: 'Web & information retrieval' }],
    systems: ['Systems', { arch: 'Computer architecture', networks: 'Computer networks', security: 'Computer security', db: 'Databases', eda: 'Design automation', embedded: 'Embedded & real-time systems', hpc: 'High-performance computing', mobile: 'Mobile computing', metrics: 'Measurement & performance', os: 'Operating systems', pl: 'Programming languages', se: 'Software engineering' }],
    theory: ['Theory', { theory: 'Algorithms & complexity', crypto: 'Cryptography', logic: 'Logic & verification' }],
    interdisciplinary: ['Interdisciplinary', { bio: 'Computational biology', graphics: 'Computer graphics', csed: 'CS education', ecom: 'Economics & computation', hci: 'Human-computer interaction', robotics: 'Robotics', visualization: 'Visualization' }],
  };
  const SUB_LABEL = Object.fromEntries(Object.values(AREAS).flatMap(([, subs]) => Object.entries(subs)));
  const SRC_LABEL = { sheet: 'Curated', wikidata: 'Wikidata', wikipedia: 'Wikipedia' };
  const MAX_CHILDREN = 14;
  const PAGE = 60;

  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : document.createTextNode(k));
    return n;
  };
  const fold = (s) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();

  let G; // data
  const state = { areas: new Set(), unis: new Set(), onlyKnown: false, sort: 'lineage', shown: PAGE, root: null, expanded: new Set() };

  /* ---------- data ---------- */
  async function load() {
    const res = await fetch('data/graph.json');
    const raw = await res.json();
    const nodes = new Map();
    for (const n of raw.nodes) {
      n.ext = !!n.x;
      n.f = fold(n.n);
      if (!n.ext) { n.uni = raw.unis[n.u][0]; n.uniShort = raw.unis[n.u][1] || raw.unis[n.u][0]; }
      n.adv = []; n.stu = [];
      nodes.set(n.id, n);
    }
    for (const [a, s, t, src, url, disputed] of raw.edges) {
      const e = { a, s, t, src: src.split('+'), url, disputed: !!disputed };
      nodes.get(a).stu.push(e);
      nodes.get(s).adv.push(e);
    }
    for (const n of nodes.values()) {
      n.knownAdv = n.adv.some((e) => !e.disputed);
      n.degree = n.adv.length + n.stu.length;
    }
    const profs = [...nodes.values()].filter((n) => !n.ext);
    G = { nodes, profs, unis: raw.unis, summary: raw.summary };
  }

  /* ---------- filters ---------- */
  function buildAreaFilter() {
    const box = $('#areas');
    const count = {};
    for (const p of G.profs) for (const d of p.d) count[d] = (count[d] || 0) + 1;
    for (const [area, [label, subs]] of Object.entries(AREAS)) {
      const parent = el('input', { type: 'checkbox' });
      const kids = Object.entries(subs).filter(([k]) => count[k]).map(([k, lab]) => {
        const cb = el('input', { type: 'checkbox', value: k });
        cb.addEventListener('change', () => { cb.checked ? state.areas.add(k) : state.areas.delete(k); sync(); refresh(); });
        return [cb, el('label', { class: 'check' }, cb, lab, el('small', {}, count[k]))];
      });
      const sync = () => {
        const on = kids.filter(([cb]) => cb.checked).length;
        parent.checked = on === kids.length; parent.indeterminate = on > 0 && on < kids.length;
      };
      parent.addEventListener('change', () => {
        for (const [cb] of kids) { cb.checked = parent.checked; parent.checked ? state.areas.add(cb.value) : state.areas.delete(cb.value); }
        parent.indeterminate = false; refresh();
      });
      const wrap = el('div', { class: 'area' },
        el('div', { class: 'area-head' },
          el('button', { class: 'caret', type: 'button', 'aria-label': `Expand ${label}`, onclick: () => wrap.classList.toggle('open') }, '▸'),
          el('label', { class: 'check' }, parent, label)),
        el('div', { class: 'subareas' }, kids.map(([, l]) => l)));
      box.append(wrap);
    }
  }

  function buildUniFilter() {
    const list = $('#unis');
    const count = new Map();
    for (const p of G.profs) count.set(p.u, (count.get(p.u) || 0) + 1);
    const items = G.unis.map(([full, short], i) => {
      const cb = el('input', { type: 'checkbox', value: i });
      cb.addEventListener('change', () => { cb.checked ? state.unis.add(i) : state.unis.delete(i); refresh(); });
      const label = el('label', { class: 'check' }, cb, short ? `${full} (${short})` : full, el('small', {}, count.get(i) || 0));
      return { cb, label, key: fold(`${full} ${short}`) };
    });
    items.forEach((it) => list.append(it.label));
    $('#uniQ').addEventListener('input', (ev) => {
      const q = fold(ev.target.value);
      for (const it of items) it.label.hidden = q && !it.key.includes(q);
    });
    $('#uniAll').addEventListener('click', () => { for (const it of items) if (!it.label.hidden) { it.cb.checked = true; state.unis.add(+it.cb.value); } refresh(); });
    $('#uniNone').addEventListener('click', () => { for (const it of items) it.cb.checked = false; state.unis.clear(); refresh(); });
  }

  function filtered() {
    let list = G.profs.filter((p) =>
      (!state.areas.size || p.d.some((d) => state.areas.has(d))) &&
      (!state.unis.size || state.unis.has(p.u)) &&
      (!state.onlyKnown || p.knownAdv));
    const by = {
      lineage: (a, b) => b.degree - a.degree || a.n.localeCompare(b.n),
      name: (a, b) => a.n.localeCompare(b.n),
      uni: (a, b) => a.uni.localeCompare(b.uni) || a.n.localeCompare(b.n),
    }[state.sort];
    return list.sort(by);
  }

  function refresh() { state.shown = PAGE; renderResults(); }

  function renderResults() {
    const list = filtered();
    const ol = $('#resList');
    ol.replaceChildren();
    const any = state.areas.size || state.unis.size || state.onlyKnown;
    $('#resTitle').textContent = any ? `${list.length.toLocaleString()} professors match` : 'All professors';
    if (!any) {
      const s = G.summary;
      ol.append(el('div', { class: 'stats' },
        stat(s.professors, 'professors'), stat(G.unis.length, 'institutions'),
        stat(s.with_advisor, 'with a known advisor'), stat(G.nodes.size, 'researchers in the tree')));
    }
    if (!list.length) ol.append(el('li', { class: 'empty' }, 'No professors match these filters.'));
    for (const p of list.slice(0, state.shown)) {
      const nA = p.adv.filter((e) => !e.disputed).length, nS = p.stu.length;
      ol.append(el('li', { onclick: () => go(p.id), tabindex: 0, onkeydown: (e) => e.key === 'Enter' && go(p.id) },
        el('div', {}, el('div', { class: 'res-name' }, p.n), el('div', { class: 'res-uni' }, p.uni)),
        el('div', { class: 'res-count' }, nA ? el('span', {}, el('b', {}, nA), ' advisor' + (nA > 1 ? 's' : '')) : 'advisor unknown',
          nS ? el('div', {}, el('b', {}, nS), ' student' + (nS > 1 ? 's' : '')) : null),
        el('div', { class: 'res-tags' }, p.d.map((d) => el('span', { class: 'tag' }, SUB_LABEL[d] || d)))));
    }
    $('#more').hidden = list.length <= state.shown;
  }
  const stat = (v, l) => el('div', { class: 'stat' }, el('b', {}, Number(v).toLocaleString()), el('span', {}, l));

  /* ---------- search ---------- */
  function setupSearch() {
    const q = $('#q'), box = $('#suggest');
    let hits = [], sel = -1;
    const render = () => {
      box.replaceChildren(...hits.map((n, i) => el('li', {
        role: 'option', 'aria-selected': i === sel ? 'true' : 'false',
        onmousedown: (e) => { e.preventDefault(); pick(n); },
      }, el('span', {}, n.n), el('small', {}, n.ext ? 'other researcher' : n.uniShort))));
      box.hidden = !hits.length;
    };
    const pick = (n) => { q.value = ''; hits = []; render(); q.blur(); go(n.id); };
    q.addEventListener('input', () => {
      const t = fold(q.value);
      sel = -1;
      if (t.length < 2) { hits = []; return render(); }
      const words = t.split(' ');
      const score = (n) => (n.f.startsWith(t) ? 0 : 1) + (n.ext ? 2 : 0);
      hits = [];
      for (const n of G.nodes.values()) if (words.every((w) => n.f.includes(w))) hits.push(n);
      hits.sort((a, b) => score(a) - score(b) || b.degree - a.degree).splice(12);
      render();
    });
    q.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, hits.length - 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
      else if (e.key === 'Enter' && hits.length) pick(hits[Math.max(sel, 0)]);
      else if (e.key === 'Escape') { hits = []; render(); }
    });
    q.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 100));
    q.addEventListener('focus', () => { box.hidden = !hits.length; });
  }

  /* ---------- tree ---------- */
  const svg = d3.select('#canvas');
  const gRoot = svg.append('g');
  const zoom = d3.zoom().scaleExtent([0.15, 3]).on('zoom', (ev) => gRoot.attr('transform', ev.transform));
  svg.call(zoom).on('dblclick.zoom', null);

  function buildSide(rootId, dir, depth) {
    // dir 'up' = advisors, 'down' = students
    const make = (id, d, path, edge) => {
      const n = G.nodes.get(id);
      const node = { id, n, edge, children: [] };
      if (d < depth) {
        let es = (dir === 'up' ? n.adv : n.stu).filter((e) => !path.has(dir === 'up' ? e.a : e.s));
        es = es.slice().sort((x, y) => (x.disputed - y.disputed) || (x.t === 'phd' ? -1 : 1) || (G.nodes.get(dir === 'up' ? y.a : y.s).degree - G.nodes.get(dir === 'up' ? x.a : x.s).degree));
        const key = `${dir}:${id}`;
        const limit = state.expanded.has(key) ? Infinity : d === 0 ? MAX_CHILDREN : 6;
        for (const e of es.slice(0, limit)) {
          const other = dir === 'up' ? e.a : e.s;
          node.children.push(make(other, d + 1, new Set([...path, other]), e));
        }
        if (es.length > limit) node.children.push({ more: es.length - limit, key, id: key + ':more', children: [] });
      }
      return node;
    };
    return d3.hierarchy(make(rootId, 0, new Set([rootId])), (d) => d.children);
  }

  function drawTree(fit = true) {
    const root = G.nodes.get(state.root);
    const up = +$('#up').value, down = +$('#down').value;
    const layout = d3.tree().nodeSize([30, 230]).separation((a, b) => (a.parent === b.parent ? 1 : 1.25));
    const sides = [['up', -1, layout(buildSide(root.id, 'up', up))], ['down', 1, layout(buildSide(root.id, 'down', down))]];
    gRoot.selectAll('*').remove();
    const linkG = gRoot.append('g'), nodeG = gRoot.append('g');
    for (const [dir, sign, h] of sides) {
      const pos = (d) => [sign * d.y, d.x];
      linkG.selectAll(null).data(h.links()).join('path')
        .attr('class', (l) => `link-path ${l.target.data.edge ? (l.target.data.edge.disputed ? 'disputed' : l.target.data.edge.t) : ''}`)
        .attr('d', (l) => d3.linkHorizontal()({ source: pos(l.source), target: pos(l.target) }));
      const nodes = h.descendants().filter((d) => dir === 'down' || d.depth > 0);
      const g = nodeG.selectAll(null).data(nodes).join('g')
        .attr('class', (d) => 'node ' + (d.data.more ? 'more' : d.depth === 0 ? 'root prof' : d.data.n.ext ? 'ext' : 'prof'))
        .attr('transform', (d) => `translate(${pos(d)})`)
        .on('click', (ev, d) => {
          if (d.data.more) { state.expanded.add(d.data.key); drawTree(false); }
          else if (d.depth > 0) go(d.id);
        })
        .on('mousemove', (ev, d) => showTip(ev, d))
        .on('mouseleave', hideTip);
      g.append('circle').attr('r', (d) => (d.depth === 0 ? 9 : 6));
      const anchorLeft = (d) => (d.depth === 0 ? 'middle' : sign < 0 ? 'end' : 'start');
      const dx = (d) => (d.depth === 0 ? 0 : sign * 11);
      g.append('text').attr('class', 'name').attr('x', dx).attr('dy', (d) => (d.depth === 0 ? -16 : d.data.more || d.data.n.ext ? 4 : -1))
        .attr('text-anchor', anchorLeft)
        .text((d) => (d.data.more ? `+ ${d.data.more} more` : trunc(d.data.n.n, 28)));
      g.filter((d) => !d.data.more && !d.data.n.ext).append('text').attr('class', 'sub').attr('x', dx)
        .attr('dy', (d) => (d.depth === 0 ? 24 : 11)).attr('text-anchor', anchorLeft)
        .text((d) => d.data.n.uniShort);
    }
    if (fit) fitView();
  }
  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  function fitView() {
    const box = gRoot.node().getBBox();
    const { width, height } = svg.node().getBoundingClientRect();
    if (!box.width || !width) return;
    const k = Math.min(1.1, 0.9 * Math.min(width / box.width, height / box.height));
    const t = d3.zoomIdentity.translate(width / 2 - k * (box.x + box.width / 2), height / 2 - k * (box.y + box.height / 2)).scale(k);
    svg.transition().duration(350).call(zoom.transform, t);
  }

  const tip = $('#tip');
  function showTip(ev, d) {
    if (d.data.more) { tip.textContent = 'Click to show all'; }
    else {
      const n = d.data.n, e = d.data.edge;
      tip.textContent = [n.n, n.ext ? null : n.uni, e ? `${e.t === 'phd' ? 'PhD' : 'Postdoc'} · ${e.src.map((s) => SRC_LABEL[s]).join(', ')}${e.disputed ? ' · unverified' : ''}` : null].filter(Boolean).join('\n');
    }
    tip.style.whiteSpace = 'pre-line';
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - 300) + 'px';
    tip.style.top = ev.clientY + 14 + 'px';
  }
  function hideTip() { tip.hidden = true; }

  /* ---------- detail panel ---------- */
  function renderDetail(n) {
    const d = $('#detail');
    const peopleList = (edges, otherKey) => {
      if (!edges.length) return el('p', { class: 'note' }, 'None recorded yet.');
      const sorted = edges.slice().sort((a, b) => a.disputed - b.disputed || (a.t === 'phd' ? -1 : 1));
      return el('ul', { class: 'people' }, sorted.map((e) => {
        const o = G.nodes.get(e[otherKey]);
        return el('li', {},
          el('button', { class: 'who', type: 'button', onclick: () => go(o.id) }, o.n, o.ext ? null : el('small', {}, ' · ' + o.uniShort)),
          e.t === 'postdoc' ? el('span', { class: 'src' }, 'postdoc') : null,
          e.disputed ? el('span', { class: 'src warn', title: 'From the curated sheet; conflicts with Wikidata / Wikipedia' }, 'unverified') : null,
          ...e.src.map((s) => (e.url && s !== 'sheet'
            ? el('a', { class: 'src', href: e.url, target: '_blank', rel: 'noopener' }, SRC_LABEL[s])
            : el('span', { class: 'src' }, SRC_LABEL[s]))));
      }));
    };
    const links = [];
    if (n.h) links.push(el('a', { href: n.h, target: '_blank', rel: 'noopener' }, 'Homepage'));
    if (n.g) links.push(el('a', { href: n.g, target: '_blank', rel: 'noopener' }, 'Google Scholar'));
    if (n.c) links.push(el('a', { href: n.c, target: '_blank', rel: 'noopener' }, 'CV'));
    if (n.w) links.push(el('a', { href: n.w, target: '_blank', rel: 'noopener' }, n.w.includes('wikidata') ? 'Wikidata' : 'Wikipedia'));
    d.replaceChildren(
      el('h3', {}, n.n),
      el('div', { class: 'uni' }, n.ext ? 'Not in the CSRankings faculty list' : n.uni),
      n.ext ? null : el('div', { class: 'res-tags' }, n.d.map((x) => el('span', { class: 'tag' }, SUB_LABEL[x] || x))),
      links.length ? el('div', { class: 'actions' }, links) : null,
      el('h4', {}, `Advisors (${n.adv.length})`), peopleList(n.adv, 'a'),
      el('h4', {}, `Students (${n.stu.length})`), peopleList(n.stu, 's'),
      el('p', { class: 'note' }, 'Students are only those recorded in Wikidata, Wikipedia or as advisors of other professors in this dataset, so lists are incomplete.'));
    d.hidden = false;
  }

  /* ---------- routing ---------- */
  function go(id) { location.hash = '#/p/' + encodeURIComponent(id); }
  function route() {
    const m = location.hash.match(/^#\/p\/(.+)$/);
    const id = m && decodeURIComponent(m[1]);
    const n = id && G.nodes.get(id);
    const layout = $('.layout');
    if (n) {
      state.root = id; state.expanded.clear();
      layout.classList.add('tree-mode');
      $('#results').hidden = true; $('#treeView').hidden = false;
      renderDetail(n);
      drawTree();
      document.title = `${n.n} · Academic Lineage Tree`;
      window.scrollTo({ top: 0 });
    } else {
      layout.classList.remove('tree-mode');
      $('#results').hidden = false; $('#treeView').hidden = true; $('#detail').hidden = true;
      document.title = 'Academic Lineage Tree';
    }
  }

  function about() {
    const s = G.summary;
    $('#aboutBody').replaceChildren(
      el('p', {}, 'This site maps who trained whom among computer science faculty at US universities, to help prospective PhD students understand a potential advisor\'s academic background and research family.'),
      el('ul', {},
        el('li', {}, el('b', {}, 'Faculty list: '), 'names, institutions, areas and homepages come from ', el('a', { href: 'https://csrankings.org', target: '_blank', rel: 'noopener' }, 'CSRankings'), ' (CC BY-NC-SA 4.0).'),
        el('li', {}, el('b', {}, 'Curated: '), 'advisor names collected by hand from CVs and homepages by our team.'),
        el('li', {}, el('b', {}, 'Wikidata: '), 'doctoral advisor / student statements (CC0), matched by Google Scholar ID or by name.'),
        el('li', {}, el('b', {}, 'Wikipedia: '), 'the doctoral advisor and doctoral students fields of English Wikipedia infoboxes (CC BY-SA 4.0).')),
      el('p', {}, `Currently ${Number(s.with_advisor).toLocaleString()} of ${Number(s.professors).toLocaleString()} professors have at least one known advisor. A curated advisor is marked “unverified” when Wikidata or Wikipedia lists a different advisor for the same person.`),
      el('p', {}, 'Data can be wrong or incomplete. Always confirm with the professor\'s own CV.'));
    $('#about').showModal();
  }

  /* ---------- init ---------- */
  load().then(() => {
    buildAreaFilter();
    buildUniFilter();
    setupSearch();
    renderResults();
    $('#onlyKnown').addEventListener('change', (e) => { state.onlyKnown = e.target.checked; refresh(); });
    $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; refresh(); });
    $('#more').addEventListener('click', () => { state.shown += PAGE; renderResults(); });
    $('#back').addEventListener('click', () => { location.hash = '#/'; });
    $('#up').addEventListener('change', () => drawTree());
    $('#down').addEventListener('change', () => drawTree());
    $('#zin').addEventListener('click', () => svg.transition().call(zoom.scaleBy, 1.3));
    $('#zout').addEventListener('click', () => svg.transition().call(zoom.scaleBy, 1 / 1.3));
    $('#zfit').addEventListener('click', fitView);
    $('#aboutBtn').addEventListener('click', about);
    $('#filterToggle').addEventListener('click', (e) => {
      const open = $('#sidebar').classList.toggle('open');
      e.currentTarget.setAttribute('aria-expanded', open);
      e.currentTarget.textContent = open ? 'Hide filters' : 'Filters';
    });
    addEventListener('hashchange', route);
    route();
  }).catch((err) => {
    $('#resList').replaceChildren(el('li', { class: 'empty' }, 'Could not load data: ' + err.message));
  });
})();
