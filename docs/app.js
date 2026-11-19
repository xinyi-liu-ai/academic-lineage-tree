/* Academic Lineage Family Tree — static front end following the team's Figma mock-up.
   Views: #/ (search), #/search?r=…&i=… (result cards), #/p/<id> (generation-band tree), #/favorites, #/about */
(() => {
  'use strict';

  const AREAS = {
    ai: ['AI', { ai: 'Artificial intelligence', vision: 'Computer vision', ml: 'Machine learning', nlp: 'Natural language processing', ir: 'The Web & information retrieval' }],
    systems: ['Systems', { arch: 'Computer architecture', networks: 'Computer networks', security: 'Computer security', db: 'Databases', eda: 'Design automation', embedded: 'Embedded & real-time systems', hpc: 'High-performance computing', mobile: 'Mobile computing', metrics: 'Measurement & performance', os: 'Operating systems', pl: 'Programming languages', se: 'Software engineering' }],
    theory: ['Theory', { theory: 'Algorithms & complexity', crypto: 'Cryptography', logic: 'Logic & verification' }],
    interdisciplinary: ['Interdisciplinary', { bio: 'Computational biology', graphics: 'Computer graphics', csed: 'CS education', ecom: 'Economics & computation', hci: 'Human-computer interaction', robotics: 'Robotics', visualization: 'Visualization' }],
  };
  const SUB_SHORT = { ai: 'AI core', vision: 'Vision', ml: 'ML', nlp: 'NLP', ir: 'Web & IR', arch: 'Architecture', networks: 'Networks', security: 'Security', db: 'Databases', eda: 'EDA', embedded: 'Embedded', hpc: 'HPC', mobile: 'Mobile', metrics: 'Metrics', os: 'OS', pl: 'PL', se: 'SE', theory: 'Algorithms', crypto: 'Crypto', logic: 'Logic', bio: 'Comp. bio', graphics: 'Graphics', csed: 'CS ed', ecom: 'Econ & CS', hci: 'HCI', robotics: 'Robotics', visualization: 'Visualization' };
  const SUB_LABEL = Object.fromEntries(Object.values(AREAS).flatMap(([, subs]) => Object.entries(subs)));
  const AREA_OF = Object.fromEntries(Object.entries(AREAS).flatMap(([a, [, subs]]) => Object.keys(subs).map((s) => [s, a])));
  const AREA_COLOR = { ai: '#6c9a58', systems: '#4f7d6b', theory: '#8d874a', interdisciplinary: '#5d7f99' };
  const SRC_LABEL = { sheet: 'Curated', wikidata: 'Wikidata', wikipedia: 'Wikipedia', homepage: 'Homepage', cv: 'CV', dblp: 'dblp' };
  const INFER_TIP = 'Inferred from dblp: the most frequent senior last author on this person’s papers during their PhD years. About 83% accurate when checked against known advisors.';
  const PAGE = 30;
  const MAXGEN = 6;

  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k === 'style') n.style.cssText = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false && k !== '') n.append(k.nodeType ? k : document.createTextNode(k));
    return n;
  };
  const fold = (s) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
  const fmt = (v) => Number(v).toLocaleString('en-US');

  /* ---------- favorites (this browser only) ---------- */
  const FAV_KEY = 'alt-favorites';
  let favs = new Set();
  try { favs = new Set(JSON.parse(localStorage.getItem(FAV_KEY) || '[]')); } catch (e) { favs = new Set(); }
  const saveFavs = () => { try { localStorage.setItem(FAV_KEY, JSON.stringify([...favs])); } catch (e) { /* storage unavailable */ } updateFavCount(); };
  const toggleFav = (id) => { favs.has(id) ? favs.delete(id) : favs.add(id); saveFavs(); };
  function updateFavCount() {
    const c = $('#favCount');
    const n = G ? [...favs].filter((id) => G.nodes.has(id)).length : favs.size;
    c.textContent = n; c.hidden = !n;
  }

  /* ---------- data ---------- */
  let G;
  async function load() {
    const raw = await (await fetch('data/graph.json')).json();
    const nodes = new Map();
    for (const n of raw.nodes) {
      n.ext = !!n.x;
      n.f = fold(n.n);
      n.d = n.d || [];
      if (!n.ext) { n.uni = raw.unis[n.u][0]; n.uniShort = raw.unis[n.u][1] || raw.unis[n.u][0]; }
      n.former = !!n.o;
      n.adv = []; n.stu = [];
      nodes.set(n.id, n);
    }
    for (const [a, s, t, src, url, disputed, note] of raw.edges) {
      const e = { a, s, t, src: src.split('+'), url, disputed: !!disputed, note };
      e.inferred = e.src.length === 1 && e.src[0] === 'dblp';
      nodes.get(a).stu.push(e);
      nodes.get(s).adv.push(e);
    }
    // 名单外、但去向写着某所收录的美国高校（又不是博后、学生）的人：多半也在美国高校任教，只是 CSRankings 没收
    const uniNames = raw.unis.map((u) => u[0].toLowerCase()).filter((u) => u.length > 6);
    for (const n of nodes.values()) {
      if (n.ext && n.af && !/postdoc|post-doc|student|ph\.?d|fellow|intern|alum|formerly|scientist|engineer/i.test(n.af)) {
        const a = n.af.toLowerCase();
        n.usAcad = uniNames.some((u) => a.includes(u));
      }
    }
    for (const n of nodes.values()) {
      n.knownAdv = n.adv.some((e) => !e.disputed);
      n.degree = n.adv.length + n.stu.length;
    }
    const profs = [...nodes.values()].filter((n) => !n.ext);
    const subCount = {};
    for (const p of profs) if (!p.former) for (const d of p.d) subCount[d] = (subCount[d] || 0) + 1;
    const uniCount = new Map();
    for (const p of profs) if (!p.former) uniCount.set(p.u, (uniCount.get(p.u) || 0) + 1);
    G = { nodes, profs, unis: raw.unis, summary: raw.summary, subCount, uniCount };
  }

  const initials = (name) => {
    const t = name.replace(/[^\p{L}\s.-]/gu, '').split(/[\s.-]+/).filter((x) => x && !/^(jr|sr|ii|iii)$/i.test(x));
    return ((t[0] || '?')[0] + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase();
  };
  // 已确认加载失败的照片地址：重绘树、翻页时不再反复请求、也不会闪一下
  const badPhoto = new Set(), goodPhoto = new Set();
  const avatar = (n, lazy = false) => {
    const box = el('div', {
      class: n.ext ? 'avatar ext' : 'avatar', 'aria-hidden': 'true',
      style: n.ext ? null : `--av:${AREA_COLOR[AREA_OF[(n.d || [])[0]]] || '#6c9a58'}`,
    }, el('span', { class: 'ini' }, initials(n.n)));
    const src = n.ph && n.ph.replace(/^http:/, 'https:');
    if (src && !badPhoto.has(src)) {
      // 照片引用自本人主页（教授和非名单人员都适用）；加载失败、链接失效、对方禁止外链
      // 或只是个占位小图时，移除 <img>，保留姓名首字母
      const img = el('img', { src, alt: '', loading: lazy ? 'lazy' : 'eager', decoding: 'async', referrerpolicy: 'no-referrer', draggable: 'false' });
      const fail = () => { badPhoto.add(src); img.remove(); box.classList.remove('has-photo'); };
      const show = () => { goodPhoto.add(src); box.classList.add('has-photo'); };
      img.addEventListener('load', () => {
        if (img.naturalWidth < 24 || img.naturalHeight < 24) return fail();
        // 大图解码完再淡入，避免首字母先消失、圆里空白一下
        if (img.decode) img.decode().then(show, show); else show();
      });
      img.addEventListener('error', fail);
      // 已经显示过的照片在浏览器缓存里：直接显示，点树节点重绘时不再闪一下淡入
      if (goodPhoto.has(src)) box.classList.add('has-photo', 'seen');
      box.append(img);
    }
    return box;
  };
  const title = (n) => n.ti || 'Faculty';
  const isAP = (n) => !n.ext && /^Assistant (Research |Teaching )?Professor/.test(n.ti || '');
  // 主页上的招生声明：n.rc = [yes/no, 目标年份, 抓取年月, 原句, 出处]
  // 现在申请的是哪一年秋季入学：8 月以后算下一年
  const ADMIT_YEAR = (() => { const d = new Date(); return d.getFullYear() + (d.getMonth() >= 7 ? 1 : 0); })();
  const rcStale = (n) => !!(n.rc && n.rc[1] && n.rc[1] < ADMIT_YEAR);
  const recruiting = (n) => !!(n.rc && n.rc[0] === 'yes' && !rcStale(n));
  const rcLabel = (n) => (rcStale(n) ? `Recruited for ${n.rc[1]} · may be outdated` : n.rc[1] ? `Recruiting for ${n.rc[1]}` : 'Recruiting');
  const rcBadge = (n, cls = '') => {
    if (!n.rc || (n.rc[0] === 'no' && rcStale(n))) return null;
    const no = n.rc[0] === 'no';
    return el('span', { class: `rc-badge ${cls} ${no || rcStale(n) ? 'stale' : ''}`, title: `Homepage (seen ${n.rc[2]}): “${n.rc[3]}”` },
      no ? (n.rc[1] ? `Not recruiting for ${n.rc[1]}` : 'Not taking students') : rcLabel(n));
  };
  // 名单外的人：有去向写去向，导师主页列为当前学生的写 Student
  const outsideSub = (n) => (n.af ? shortAff(n.af) : n.st ? 'PhD student' : 'Position unknown');
  const shortAff = (s) => {
    let t = (s.length > 34 ? s.split(/,\s*/)[0] : s);
    if (t.length > 40) t = t.slice(0, 40).replace(/\s+\S*$/, '') + '…';
    return (t.split('(').length > t.split(')').length) ? t.replace(/\s*\([^)]*$/, '') : t;  // 别留半个括号
  };
  const interest = (n, k = 2) => n.d.slice(0, k).map((d) => SUB_LABEL[d] || d).join(', ');

  /* ---------- shared search filters ---------- */
  const S = { subs: new Set(), unis: new Set() };
  const cards = [];

  let openPanel = null;
  function closePanels() { if (openPanel) { openPanel.hidden = true; openPanel.previousElementSibling.setAttribute('aria-expanded', 'false'); openPanel = null; } for (const c of cards) c.sync(); }
  document.addEventListener('mousedown', (e) => { if (openPanel && !openPanel.parentElement.contains(e.target)) closePanels(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePanels(); closeDrawer(); } });

  function dropdown(icon, onOpen) {
    const trigger = el('button', { class: 'dd-trigger', type: 'button', 'aria-haspopup': 'true', 'aria-expanded': 'false' });
    const panel = el('div', { class: 'dd-panel', hidden: true });
    trigger.addEventListener('click', () => {
      const wasOpen = openPanel === panel;
      closePanels();
      if (!wasOpen) { onOpen(panel); panel.hidden = false; trigger.setAttribute('aria-expanded', 'true'); openPanel = panel; for (const c of cards) c.sync(); }
    });
    const setChips = (chips, placeholder, open) => {
      const caret = el('img', { class: 'caret', src: `assets/icons/icon_${open ? 'uparrow' : 'downarrow'}.png`, alt: '' });
      trigger.replaceChildren(el('img', { src: `assets/icons/${icon}`, alt: '' }),
        chips.length ? el('span', { class: 'chips' }, chips) : el('span', { class: 'ph' }, placeholder), caret);
    };
    return { box: el('div', { class: 'dd' }, trigger, panel), panel, setChips };
  }

  function chipList(labels) {
    if (labels.length <= 2) return labels.map((l) => el('span', { class: 'chip' }, l));
    return [el('span', { class: 'chip' }, labels[0]), el('span', { class: 'chip' }, labels[1]), el('span', { class: 'dots' }, '…'), el('span', { class: 'chip count' }, labels.length)];
  }

  function areaLabels() {
    const out = [];
    for (const [, [lab, subs]] of Object.entries(AREAS)) {
      const ks = Object.keys(subs).filter((k) => G.subCount[k]);
      const on = ks.filter((k) => S.subs.has(k));
      if (on.length && on.length === ks.length) out.push(lab);
      else out.push(...on.map((k) => SUB_SHORT[k]));
    }
    return out;
  }

  function buildAreaPanel(panel, refresh) {
    const rows = [];
    const all = el('input', { type: 'checkbox' });
    const allKeys = Object.keys(SUB_LABEL).filter((k) => G.subCount[k]);
    const sync = () => {
      all.checked = S.subs.size === allKeys.length; all.indeterminate = S.subs.size > 0 && !all.checked;
      for (const r of rows) r();
      refresh();
    };
    all.addEventListener('change', () => { S.subs = new Set(all.checked ? allKeys : []); sync(); });
    panel.replaceChildren(el('label', { class: 'row area' }, all, 'ALL'));
    for (const [, [lab, subs]] of Object.entries(AREAS)) {
      const ks = Object.keys(subs).filter((k) => G.subCount[k]);
      const acb = el('input', { type: 'checkbox' });
      const subRows = ks.map((k) => {
        const cb = el('input', { type: 'checkbox' });
        cb.addEventListener('change', () => { cb.checked ? S.subs.add(k) : S.subs.delete(k); sync(); });
        rows.push(() => { cb.checked = S.subs.has(k); });
        return el('label', { class: 'row sub', hidden: true }, cb, subs[k], el('small', {}, fmt(G.subCount[k])));
      });
      const exp = el('button', { class: 'exp', type: 'button', 'aria-label': `Expand ${lab}` }, '+');
      exp.addEventListener('click', (e) => {
        e.preventDefault();
        const open = subRows[0] && subRows[0].hidden;
        for (const r of subRows) r.hidden = !open;
        exp.textContent = open ? '−' : '+';
      });
      acb.addEventListener('change', () => { for (const k of ks) acb.checked ? S.subs.add(k) : S.subs.delete(k); sync(); });
      rows.push(() => {
        const on = ks.filter((k) => S.subs.has(k)).length;
        acb.checked = on === ks.length; acb.indeterminate = on > 0 && on < ks.length;
      });
      panel.append(el('div', { class: 'row area' }, exp, el('label', { class: 'area-label' }, acb, lab)), ...subRows);
    }
    sync();
  }

  function buildUniPanel(panel, refresh) {
    const q = el('input', { type: 'search', placeholder: 'Search institutions', 'aria-label': 'Search institutions' });
    const list = el('div');
    const items = G.unis.map(([full, short], i) => ({ i, full, short, key: fold(`${full} ${short}`) }))
      .sort((a, b) => a.full.localeCompare(b.full));
    const render = () => {
      const t = fold(q.value);
      list.replaceChildren(...items.filter((it) => !t || it.key.includes(t)).map((it) => {
        const cb = el('input', { type: 'checkbox' });
        cb.checked = S.unis.has(it.i);
        cb.addEventListener('change', () => { cb.checked ? S.unis.add(it.i) : S.unis.delete(it.i); refresh(); });
        return el('label', { class: 'row' }, cb, it.full, el('small', {}, fmt(G.uniCount.get(it.i) || 0)));
      }));
    };
    q.addEventListener('input', render);
    const selAll = el('button', { class: 'linkbtn', type: 'button' }, 'Select all');
    selAll.addEventListener('click', () => { const t = fold(q.value); for (const it of items) if (!t || it.key.includes(t)) S.unis.add(it.i); render(); refresh(); });
    const clear = el('button', { class: 'linkbtn', type: 'button' }, 'Clear');
    clear.addEventListener('click', () => { S.unis.clear(); render(); refresh(); });
    panel.replaceChildren(el('div', { class: 'uni-search' }, q, selAll, clear), list);
    render();
    setTimeout(() => q.focus(), 0);
  }

  function mountSearch(box, variant) {
    const area = dropdown('icon_book.png', (panel) => buildAreaPanel(panel, () => syncChips()));
    const uni = dropdown('icon_institution.png', (panel) => buildUniPanel(panel, () => syncChips()));
    const syncChips = () => {
      area.setChips(chipList(areaLabels()), 'Choose a tag.', openPanel === area.panel);
      const u = [...S.unis].map((i) => G.unis[i][1] || G.unis[i][0]);
      uni.setChips(u.length ? chipList(u) : [el('span', { class: 'chip' }, 'All')], '', openPanel === uni.panel);
    };
    const go = el('button', { class: 'go', type: 'button', 'aria-label': 'Search' }, el('img', { src: 'assets/icons/icon_search.png', alt: '' }));
    go.addEventListener('click', () => { closePanels(); location.hash = searchHash(); });
    box.append(el('div', { class: 'search-row' },
      el('div', { class: 'field' }, el('span', { class: 'lab' }, 'Research Interest'), area.box),
      el('div', { class: 'field' }, el('span', { class: 'lab' }, 'Institution'), uni.box),
      go));
    if (variant === 'home') box.append(nameSearch());
    cards.push({ sync: syncChips });
    syncChips();
  }

  function nameSearch() {
    const input = el('input', { type: 'search', placeholder: 'Or jump to a professor by name, e.g. Michael I. Jordan', autocomplete: 'off', 'aria-label': 'Search professors by name' });
    const box = el('ul', { class: 'suggest', role: 'listbox', hidden: true });
    let hits = [], sel = -1;
    const render = () => {
      box.replaceChildren(...hits.map((n, i) => el('li', {
        role: 'option', 'aria-selected': i === sel ? 'true' : 'false',
        onmousedown: (e) => { e.preventDefault(); pick(n); },
      }, el('span', {}, n.n), el('small', {}, n.ext ? 'other researcher' : n.uniShort + (n.former ? ' (former)' : '')))));
      box.hidden = !hits.length;
    };
    const pick = (n) => { input.value = ''; hits = []; render(); go(n.id); };
    input.addEventListener('input', () => {
      const t = fold(input.value);
      if (t.length < 2) { hits = []; render(); return; }
      const words = t.split(' ');
      const score = (n) => (n.f.startsWith(t) ? 0 : n.f.split(' ').some((w) => w.startsWith(words[0])) ? 1 : 2) + (n.ext ? 3 : 0) + (n.former ? 1 : 0);
      hits = [];
      for (const n of G.nodes.values()) if (words.every((w) => n.f.includes(w))) hits.push(n);
      hits.sort((a, b) => score(a) - score(b) || b.degree - a.degree);
      hits = hits.slice(0, 8); sel = hits.length ? 0 : -1; render();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { sel = Math.min(hits.length - 1, sel + 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); render(); e.preventDefault(); }
      else if (e.key === 'Enter' && hits[sel]) pick(hits[sel]);
    });
    input.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 120));
    return el('div', { class: 'name-row' }, el('span', { class: 'glass' }), input, box);
  }

  // URL 里的机构用全名（可读、可分享，数据重排后也不失效）；也接受旧链接里的序号和简称（如 CMU），大小写、重音不敏感
  let uniKey = null;
  const uniIndex = (v) => {
    const s = v.trim();
    if (!s) return null;
    if (/^\d+$/.test(s)) return G.unis[+s] ? +s : null;
    if (!uniKey) {
      uniKey = new Map();
      G.unis.forEach(([full, short], i) => { uniKey.set(fold(full), i); if (short && !uniKey.has(fold(short))) uniKey.set(fold(short), i); });
    }
    const k = uniKey.get(fold(s));
    return k == null ? null : k;
  };
  const searchHash = () => {
    // 逐项编码、逗号保留原样，地址栏里更好读：?i=Northeastern%20University,Carnegie%20Mellon%20University
    const p = [];
    if (S.subs.size) p.push('r=' + [...S.subs].join(','));
    if (S.unis.size) p.push('i=' + [...S.unis].map((i) => encodeURIComponent(G.unis[i][0])).join(','));
    const q = p.join('&');
    return '#/search' + (q ? '?' + q : '');
  };

  /* ---------- results ---------- */
  const R = { onlyKnown: false, onlyAP: false, onlyRC: false, sort: 'lineage', shown: PAGE, name: '' };
  function filtered() {
    const t = fold(R.name);
    const list = G.profs.filter((p) =>
      (!S.subs.size || p.d.some((d) => S.subs.has(d))) &&
      (!S.unis.size || S.unis.has(p.u)) &&
      (!R.onlyKnown || p.knownAdv) &&
      (!R.onlyAP || isAP(p)) &&
      (!R.onlyRC || recruiting(p)) &&
      !p.former &&
      (!t || p.f.includes(t)));
    const by = {
      lineage: (a, b) => b.degree - a.degree || a.n.localeCompare(b.n),
      name: (a, b) => a.n.localeCompare(b.n),
      uni: (a, b) => a.uni.localeCompare(b.uni) || a.n.localeCompare(b.n),
    }[R.sort];
    return list.sort(by);
  }

  function card(p) {
    const nA = p.adv.filter((e) => !e.disputed).length, nS = p.stu.length;
    const star = el('button', { class: `corner ${favs.has(p.id) ? 'on' : ''}`, type: 'button', 'aria-pressed': favs.has(p.id) ? 'true' : 'false', 'aria-label': `Star ${p.n}` }, el('span', { class: 'star' }));
    star.addEventListener('click', (e) => {
      e.stopPropagation(); toggleFav(p.id);
      star.classList.toggle('on', favs.has(p.id)); star.setAttribute('aria-pressed', favs.has(p.id) ? 'true' : 'false');
      if (current.view === 'favorites') renderFavorites();
    });
    return el('li', { class: 'card', tabindex: 0, onclick: () => go(p.id), onkeydown: (e) => { if (e.key === 'Enter') go(p.id); } },
      avatar(p, true), star,
      el('div', { class: 'card-body' },
        el('div', { class: 'card-name' }, p.n),
        el('div', { class: 'card-meta' },
          el('div', { class: isAP(p) ? 'is-ap' : null }, title(p)),
          rcBadge(p) ? el('div', {}, rcBadge(p)) : null,
          el('div', {}, p.uni),
          el('div', { class: 'interest' }, interest(p) || '—')),
        el('div', { class: 'card-stats' }, nA ? `${nA} advisor${nA > 1 ? 's' : ''}` : 'Advisor unknown', nS ? ` · ${nS} student${nS > 1 ? 's' : ''}` : '')));
  }

  function renderResults() {
    const list = filtered();
    $('#resCount').textContent = `${fmt(list.length)} Results found...`;
    const ul = $('#cards');
    ul.replaceChildren(...list.slice(0, R.shown).map(card));
    if (!list.length) ul.append(el('li', { class: 'empty' }, 'No professors match. Try fewer tags or another institution.'));
    $('#more').hidden = list.length <= R.shown;
  }

  function renderFavorites() {
    const ul = $('#favCards');
    const list = [...favs].map((id) => G.nodes.get(id)).filter((n) => n && !n.ext);
    ul.replaceChildren(...list.map(card));
    if (!list.length) ul.append(el('li', { class: 'empty' }, 'No favorites yet. Use the star on a professor’s card or in the tree to keep them here.'));
  }

  /* ---------- tree (generation bands) ---------- */
  const T = { root: null, focus: { up: [], down: [] }, page: {}, lo: null, zoom: 1, selected: null };
  const edgeOther = (e, dir) => (dir === 'down' ? e.s : e.a);
  const edgeSort = (dir) => (x, y) => (x.disputed - y.disputed) || (x.inferred - y.inferred) || ((x.t === 'phd' ? 0 : 1) - (y.t === 'phd' ? 0 : 1)) ||
    (G.nodes.get(edgeOther(y, dir)).degree - G.nodes.get(edgeOther(x, dir)).degree);

  function layoutParams() {
    const vp = $('#viewport');
    const w = vp.clientWidth / T.zoom, h = vp.clientHeight / T.zoom;
    let n = Math.max(3, Math.min(7, Math.floor(w / 170)));
    if (n % 2 === 0) n -= 1;
    const av = w / n < 140 ? 44 : 60;
    const perPage = Math.max(2, Math.floor((h - 90) / (av + 66)));
    return { n, av, perPage };
  }

  function columns(perPage) {
    const cols = new Map([[0, { items: [{ id: T.root, edge: null }], parent: null }]]);
    for (const dir of ['down', 'up']) {
      const sign = dir === 'down' ? 1 : -1;
      const chain = new Set([T.root]);
      let parent = T.root;
      for (let k = 1; k <= MAXGEN; k++) {
        const node = G.nodes.get(parent);
        const es = (dir === 'down' ? node.stu : node.adv).filter((e) => !chain.has(edgeOther(e, dir))).sort(edgeSort(dir));
        if (!es.length) break;
        const gen = sign * k;
        const items = es.map((e) => ({ id: edgeOther(e, dir), edge: e }));
        const pages = Math.ceil(items.length / perPage);
        let pg = Math.min(T.page[gen] || 0, pages - 1);
        let f = T.focus[dir][k - 1];
        const fi = items.findIndex((it) => it.id === f);
        if (fi < 0) {
          const onPage = items.slice(pg * perPage, (pg + 1) * perPage);
          const more = (it) => { const n = G.nodes.get(it.id); return (dir === 'down' ? n.stu : n.adv).some((e) => !chain.has(edgeOther(e, dir)) && edgeOther(e, dir) !== it.id); };
          f = (onPage.find(more) || onPage[0]).id;
          T.focus[dir][k - 1] = f;
          T.focus[dir].length = k;
        } else if (T.page[gen] == null) {
          pg = Math.floor(fi / perPage);
        }
        T.page[gen] = pg;
        cols.set(gen, { items, parent, pages, page: pg });
        chain.add(f);
        parent = f;
      }
    }
    return cols;
  }

  function genLabel(gen, col) {
    if (gen === 0 || (!col && Math.abs(gen) > 1)) return '';
    const k = Math.abs(gen);
    const pre = k === 1 ? '' : k === 2 ? 'Grand-' : 'Great-'.repeat(k - 2) + 'grand-';
    const base = gen < 0 ? 'advisors' : 'students';
    return (pre ? pre + base : base[0].toUpperCase() + base.slice(1)).replace(/^g/, 'G');
  }
  const genTitle = (gen, col) => (col && Math.abs(gen) > 1 ? `${gen < 0 ? 'Advisors' : 'Students'} of ${G.nodes.get(col.parent).n}` : genLabel(gen, col));

  const shortTitle = (t) => t.replace(/ Professor\b/, ' Prof.').replace(/^Professor\b/, 'Prof.');

  function tnode(it, gen, isFocus) {
    const n = G.nodes.get(it.id), e = it.edge;
    const badge = e ? (e.disputed ? el('span', { class: 't-badge warn' }, 'unverified') : e.inferred ? el('span', { class: 't-badge' }, 'inferred') : e.t === 'postdoc' ? el('span', { class: 't-badge' }, 'postdoc') : null) : null;
    // 在美国高校任教的人醒目显示（AP 另外高亮，最可能在招生）；业界、国外、学生淡化
    const role = n.ext ? (n.usAcad ? 'fac' : 'out') : isAP(n) ? 'fac ap' : 'fac';
    const b = el('button', { class: `tnode ${role} ${gen === 0 ? 'root' : ''} ${isFocus ? 'focus' : ''}`, type: 'button', 'data-id': it.id, 'data-gen': gen },
      avatar(n),
      el('span', { class: 't-name' }, n.n),
      !n.ext && n.ti ? el('span', { class: 't-title' }, shortTitle(n.ti)) : null,
      el('span', { class: 't-sub' }, n.ext ? outsideSub(n) : n.uniShort),
      gen === 0 && !n.ext ? el('span', { class: 't-dir' }, n.d[0] ? SUB_LABEL[n.d[0]] : '') : null,
      rcBadge(n, 't-rc'),
      badge);
    b.addEventListener('click', () => selectNode(it.id, gen));
    b.addEventListener('dblclick', () => { if (gen !== 0) go(it.id); });
    b.addEventListener('mousemove', (ev) => showTip(ev, n, e));
    b.addEventListener('mouseleave', hideTip);
    return b;
  }

  function drawTree() {
    if (!T.root) return;
    const { n, av, perPage } = layoutParams();
    const cols = columns(perPage);
    const gens = [...cols.keys()];
    const minGen = Math.min(...gens), maxGen = Math.max(...gens);
    if (T.lo == null) {
      let lo = -Math.floor(n / 2);
      const hi = lo + n - 1;
      if (minGen > lo) lo += Math.min(minGen - lo, Math.max(0, maxGen - hi));
      else if (maxGen < hi) lo -= Math.min(hi - maxGen, Math.max(0, lo - minGen));
      T.lo = lo;
    }
    T.lo = Math.max(Math.min(T.lo, Math.max(maxGen - n + 1, -n + 1)), Math.min(minGen, 0));
    const lo = T.lo, hi = lo + n - 1;
    $('#genLeft').disabled = lo <= minGen;
    $('#genRight').disabled = hi >= maxGen;

    const bands = $('#bands');
    bands.style.setProperty('--av-size', av + 'px');
    bands.style.setProperty('--root-size', (av + 36) + 'px');
    bands.replaceChildren();
    for (let g = lo; g <= hi; g++) {
      const col = cols.get(g);
      const band = el('div', { class: `band d${Math.min(Math.abs(g), 2)}` });
      const label = genLabel(g, col);
      if (label) band.append(el('div', { class: 'gen-label', title: genTitle(g, col) }, label + (col && col.items.length > 1 ? ` · ${col.items.length}` : '')));
      if (col) {
        const dir = g > 0 ? 'down' : 'up';
        const f = g === 0 ? null : T.focus[dir][Math.abs(g) - 1];
        const items = g === 0 ? col.items : col.items.slice(col.page * perPage, (col.page + 1) * perPage);
        band.append(el('div', { class: 'stack' }, items.map((it) => tnode(it, g, it.id === f && cols.has(g + Math.sign(g))))));
        if (col.pages > 1) {
          const prev = el('button', { type: 'button', 'aria-label': 'Previous page' }, '‹');
          const next = el('button', { type: 'button', 'aria-label': 'Next page' }, '›');
          prev.disabled = col.page === 0; next.disabled = col.page >= col.pages - 1;
          const turn = (d) => {
            T.page[g] = col.page + d;
            T.focus[dir].length = Math.abs(g) - 1;
            for (const k of Object.keys(T.page)) if (Math.sign(+k) === Math.sign(g) && Math.abs(+k) > Math.abs(g)) delete T.page[k];
            drawTree();
          };
          prev.addEventListener('click', () => turn(-1)); next.addEventListener('click', () => turn(1));
          band.append(el('div', { class: 'pager' }, prev, `${col.page + 1} / ${col.pages}`, next));
        }
      } else if (g === -1) band.append(el('div', { class: 'empty-col' }, 'No advisor recorded yet'));
      else if (g === 1) band.append(el('div', { class: 'empty-col' }, 'No students recorded yet'));
      bands.append(band);
    }
    requestAnimationFrame(drawLinks);
  }

  function drawLinks() {
    const svg = $('#links'), box = $('#scaleBox');
    const br = box.getBoundingClientRect(), z = T.zoom;
    const pos = (node) => {
      const r = node.querySelector('.avatar').getBoundingClientRect();
      return { l: (r.left - br.left) / z, r: (r.right - br.left) / z, y: (r.top + r.height / 2 - br.top) / z };
    };
    const byId = (gen, id) => $(`.tnode[data-gen="${gen}"][data-id="${CSS.escape(id)}"]`, $('#bands'));
    const paths = [];
    for (const node of $('#bands').querySelectorAll('.tnode')) {
      const g = +node.dataset.gen;
      if (!g) continue;
      const dir = g > 0 ? 'down' : 'up';
      const parentId = Math.abs(g) === 1 ? T.root : T.focus[dir][Math.abs(g) - 2];
      const pn = byId(g - Math.sign(g), parentId);
      if (!pn) continue;
      const a = pos(pn), b = pos(node);
      const x1 = g > 0 ? a.r : a.l, x2 = g > 0 ? b.l : b.r;
      const mx = (x1 + x2) / 2;
      const n = G.nodes.get(node.dataset.id);
      const e = (g > 0 ? n.adv : n.stu).find((x) => (g > 0 ? x.a : x.s) === parentId);
      const cls = !e ? '' : e.disputed ? 'disputed' : e.inferred ? 'inferred' : e.t === 'postdoc' ? 'postdoc' : '';
      paths.push(`<path class="${cls}" d="M${x1},${a.y} C${mx},${a.y} ${mx},${b.y} ${x2},${b.y}"/>`);
    }
    svg.setAttribute('viewBox', `0 0 ${box.offsetWidth} ${box.offsetHeight}`);
    svg.innerHTML = paths.join('');
  }

  function selectNode(id, gen) {
    if (gen !== 0) {
      const dir = gen > 0 ? 'down' : 'up', k = Math.abs(gen);
      T.focus[dir][k - 1] = id;
      T.focus[dir].length = k;
      for (const key of Object.keys(T.page)) if (Math.sign(+key) === Math.sign(gen) && Math.abs(+key) > k) delete T.page[key];
    }
    T.selected = id;
    drawTree();
    renderDrawer(id);
  }

  function relationTo(id) {
    if (id === T.root) return null;
    const root = G.nodes.get(T.root);
    const asAdv = root.adv.find((e) => e.a === id);
    if (asAdv) return [asAdv, `${asAdv.t === 'phd' ? 'PhD' : 'Postdoc'} advisor of ${root.n}`];
    const asStu = root.stu.find((e) => e.s === id);
    if (asStu) return [asStu, `${asStu.t === 'phd' ? 'PhD' : 'Postdoc'} student of ${root.n}`];
    return null;
  }

  const flagBadges = (e) => [
    e.t === 'postdoc' ? el('span', { class: 'src' }, 'postdoc') : null,
    e.disputed ? el('span', { class: 'src warn', title: 'From the curated sheet; conflicts with other sources' }, 'unverified') : null,
    e.inferred ? el('span', { class: 'src infer', title: INFER_TIP + (e.note ? ` (${e.note})` : '') }, 'inferred') : null,
  ];
  const REPO = 'https://github.com/xinyi-liu-ai/academic-lineage-tree';
  const suggestUrl = (n) => `${REPO}/issues/new?` + new URLSearchParams({
    title: `Correction / addition: ${n.n}`,
    body: `Person: ${n.n}${n.uni ? ' (' + n.uni + ')' : ''}
Page: ${location.href}

What is missing or wrong (e.g. a student, an advisor, a homepage)?

Source (homepage, CV, thesis…):
`,
  }).toString();
  const linkedinSearch = (name) => 'https://www.linkedin.com/search/results/people/?keywords=' + encodeURIComponent(name);
  // 名字直接链到个人主页；没有主页就链到领英（档案或按姓名搜索）
  const nameLink = (o) => el('a', {
    class: 'p-name', href: o.h || o.li || linkedinSearch(o.n), target: '_blank', rel: 'noopener',
    title: o.h ? 'Homepage' : o.li ? 'LinkedIn' : 'No homepage found; search LinkedIn',
  }, o.n);
  const scholarLink = (o) => (o.g ? el('a', { class: 'plink', href: o.g, target: '_blank', rel: 'noopener' }, 'Google Scholar') : null);

  function peopleList(edges, otherKey) {
    if (!edges.length) return el('p', { class: 'note' }, 'None recorded yet.');
    const sorted = edges.slice().sort((a, b) => a.disputed - b.disputed || a.inferred - b.inferred || (a.t === 'phd' ? -1 : 1));
    const LIMIT = 12;
    const ul = el('ul', { class: 'people' });
    const render = (all) => ul.replaceChildren(...sorted.slice(0, all ? Infinity : LIMIT).map((e) => {
      const o = G.nodes.get(e[otherKey]);
      return el('li', {}, nameLink(o),
        el('small', {}, o.ext ? (o.af ? shortAff(o.af) : '') : o.uniShort), scholarLink(o), ...flagBadges(e),
        el('button', { class: 'tree-btn', type: 'button', title: `Open ${o.n}’s family tree`, 'aria-label': `Open ${o.n}’s family tree`, onclick: () => go(o.id) },
          el('img', { src: 'assets/tree-icon.png', alt: '' })));
    }), ...(sorted.length > LIMIT && !all ? [el('li', {}, el('button', { class: 'more-people', type: 'button', onclick: () => render(true) }, `Show all ${sorted.length}`))] : []));
    render(false);
    return ul;
  }

  function renderDrawer(id) {
    const n = G.nodes.get(id), d = $('#drawer');
    const rel = relationTo(id);
    const links = [];
    const ext = (href, text, cls) => el('a', { href, target: '_blank', rel: 'noopener', class: cls || null }, text);
    if (n.h) { const a = ext(n.h, 'Homepage', 'primary'); if (n.hv) a.title = 'Linked from ' + n.hv; links.push(a); }
    if (n.li) links.push(ext(n.li, 'LinkedIn'));
    else if (!n.h || n.ext) links.push(ext(linkedinSearch(n.n), 'Find on LinkedIn'));
    if (n.g) links.push(ext(n.g, 'Google Scholar'));
    if (n.c) links.push(ext(n.c, 'CV'));
    const fav = n.ext ? null : el('button', { class: `fav ${favs.has(id) ? 'on' : ''}`, type: 'button' }, el('span', { class: 'star' }), favs.has(id) ? 'Starred' : 'Star');
    if (fav) fav.addEventListener('click', () => { toggleFav(id); renderDrawer(id); });
    d.replaceChildren(...[
      el('button', { class: 'close', type: 'button', 'aria-label': 'Close details', onclick: closeDrawer }, '×'),
      el('div', { class: 'who' }, avatar(n), el('div', {}, el('h3', {}, n.n),
        n.ext ? null : el('div', { class: 'uni ti' }, title(n)), el('div', { class: 'uni' }, n.ext ? (n.af || (n.st ? 'PhD student (listed on an advisor’s page)' : 'Current position unknown')) : n.uni))),
      n.rc ? el('div', { class: `rc-box ${n.rc[0]}` },
        el('b', {}, n.rc[0] === 'yes' ? rcLabel(n) : 'Not taking students'), ` — homepage, seen ${n.rc[2]}`,
        el('q', {}, n.rc[3]), n.rc[4] ? el('a', { href: n.rc[4], target: '_blank', rel: 'noopener' }, 'Source') : null) : null,
      n.ext ? el('p', { class: 'note' }, 'Not on the CSRankings faculty list of these 189 US universities (for example in industry, abroad or another field).') : null,
      n.former ? el('p', { class: 'note' }, 'No longer on CSRankings’ current faculty list for these institutions (moved, retired or emeritus)' + (n.now ? `; now listed at ${n.now}.` : '.')) : null,
      rel ? el('p', { class: 'note' }, rel[1], ' ', ...flagBadges(rel[0])) : null,
      n.ext || !n.d.length ? null : el('div', { class: 'tags' }, n.d.map((x) => el('span', { class: 'tag' }, SUB_LABEL[x] || x))),
      links.length ? el('div', { class: 'links-row' }, links) : null,
      el('div', { class: 'actions' }, fav, id !== T.root ? el('button', { class: 'btn-dark', type: 'button', onclick: () => go(id) }, 'View this family tree') : null),
      el('h4', {}, `Advisors (${n.adv.length})`), peopleList(n.adv, 'a'),
      el('h4', {}, `Students (${n.stu.length})`), peopleList(n.stu, 's'),
      el('p', { class: 'note' }, 'Student lists are incomplete: they only include students we could find in public records. ',
        el('a', { href: suggestUrl(n), target: '_blank', rel: 'noopener' }, 'Missing someone or something wrong?'))].filter(Boolean));
    d.classList.add('open');
    d.scrollTop = 0;
  }
  function closeDrawer() { $('#drawer').classList.remove('open'); }

  const tip = $('#tip');
  function showTip(ev, n, e) {
    tip.textContent = [n.n, n.ext ? (n.af || null) : (n.ti ? n.ti + ', ' : '') + n.uni + (n.former ? ' (former)' : ''),
      e ? `${e.t === 'phd' ? 'PhD' : 'Postdoc'}${e.disputed ? ' · unverified' : ''}${e.inferred ? ' · inferred from co-authorship' : ''}` : null,
      'Click for details · double-click to re-center'].filter(Boolean).join('\n');
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - 290) + 'px';
    tip.style.top = ev.clientY + 14 + 'px';
  }
  function hideTip() { tip.hidden = true; }

  function openTree(id) {
    if (T.root !== id) Object.assign(T, { root: id, focus: { up: [], down: [] }, page: {}, lo: null, selected: id });
    const n = G.nodes.get(id);
    $('#treeTitle').replaceChildren(n.n, el('small', {}, n.ext ? 'other researcher' : n.uni + (n.former ? ' (former)' : '')));
    document.title = `${n.n} · Academic Lineage Family Tree`;
    closeDrawer();
    drawTree();
    renderFamily();
  }

  /* ---------- academic family list ---------- */
  // 从中心人物出发，沿师生关系（上 = 导师方向，下 = 学生方向）做广度优先搜索，记下最短路径的上下序列
  const F = { dist: 2, ap: false, rc: false, area: false, all: false, cache: null };
  const REL = {
    u: 'Advisor', uu: 'Grand-advisor', uuu: 'Great-grand-advisor', uuuu: 'Great-great-grand-advisor',
    d: 'Student', dd: 'Grand-student', ddd: 'Great-grand-student', dddd: 'Great-great-grand-student',
    ud: 'Lab sibling (same advisor)', udd: 'Student of a lab sibling', uddd: 'Grand-student of a lab sibling',
    uud: 'Advisor’s lab sibling', uudd: 'Academic cousin', uuud: 'Grand-advisor’s lab sibling',
    du: 'Co-advisor of a student', dud: 'Student of a co-advisor', duu: 'Advisor of a co-advisor',
  };
  const relLabel = (sig) => REL[sig] || `${sig.length} steps away`;

  function familyOf(rootId, maxDist) {
    const seen = new Map([[rootId, '']]);
    let frontier = [rootId];
    for (let k = 1; k <= maxDist && frontier.length; k++) {
      const next = [];
      for (const id of frontier) {
        const n = G.nodes.get(id), sig = seen.get(id);
        for (const e of n.adv) if (!e.disputed && !seen.has(e.a)) { seen.set(e.a, sig + 'u'); next.push(e.a); }
        for (const e of n.stu) if (!e.disputed && !seen.has(e.s)) { seen.set(e.s, sig + 'd'); next.push(e.s); }
      }
      frontier = next;
    }
    seen.delete(rootId);
    return seen;
  }

  function renderFamily() {
    const root = G.nodes.get(T.root);
    if (!root) return;
    if (!F.cache || F.cache.root !== T.root) F.cache = { root: T.root, map: familyOf(T.root, 4) };
    for (const b of document.querySelectorAll('.fam-controls [data-dist]')) b.classList.toggle('on', +b.dataset.dist === F.dist);
    const areas = new Set(root.ext ? [] : root.a);
    $('#famArea').disabled = !areas.size;
    const rows = [];
    for (const [id, sig] of F.cache.map) {
      if (sig.length > F.dist) continue;
      const n = G.nodes.get(id);
      if (!F.all && n.ext && !n.usAcad) continue;
      if (F.ap && !isAP(n)) continue;
      if (F.rc && !recruiting(n)) continue;
      if (F.area && areas.size && (n.ext || !n.a.some((a) => areas.has(a)))) continue;
      rows.push({ n, sig });
    }
    // 近的在前；同样远时招生的、AP 在前
    rows.sort((x, y) => x.sig.length - y.sig.length || recruiting(y.n) - recruiting(x.n) || isAP(y.n) - isAP(x.n)
      || ((x.n.ext && !x.n.usAcad) - (y.n.ext && !y.n.usAcad)) || (x.n.ext - y.n.ext) || y.n.degree - x.n.degree);
    const fac = rows.filter((r) => !r.n.ext || r.n.usAcad).length;
    $('#famTitle').textContent = `${root.n}’s academic family`;
    $('#famSub').textContent = `${fmt(fac)} US faculty within ${F.dist} step${F.dist > 1 ? 's' : ''}` +
      (F.all ? ` · ${fmt(rows.length - fac)} others` : '') +
      ` · ${fmt(rows.filter((r) => isAP(r.n)).length)} assistant professor${rows.filter((r) => isAP(r.n)).length === 1 ? '' : 's'}` +
      ` · ${fmt(rows.filter((r) => recruiting(r.n)).length)} ${rows.filter((r) => recruiting(r.n)).length === 1 ? 'says' : 'say'} recruiting`;
    const LIMIT = 200;
    $('#famList').replaceChildren(...(rows.length ? rows.slice(0, LIMIT).map(({ n, sig }) => {
      const li = el('li', { class: `fam-row d${Math.min(sig.length, 4)} ${n.ext && !n.usAcad ? 'out' : ''}` },
        el('button', { class: 'fam-main', type: 'button', onclick: () => renderDrawer(n.id), title: 'Show details' },
          avatar(n, true),
          el('span', { class: 'fam-text' },
            el('span', { class: 'fam-name' }, n.n),
            el('span', { class: 'fam-rel' }, relLabel(sig)),
            el('span', { class: 'fam-meta', title: n.ext ? n.af || '' : '' }, n.ext ? outsideSub(n) + (n.usAcad ? ' · not on CSRankings' : '') : `${shortTitle(title(n))} · ${n.uniShort}`),
            rcBadge(n))),
        el('button', { class: 'tree-btn', type: 'button', title: `Open ${n.n}’s family tree`, 'aria-label': `Open ${n.n}’s family tree`, onclick: () => go(n.id) },
          el('img', { src: 'assets/tree-icon.png', alt: '' })));
      return li;
    }) : [el('li', { class: 'fam-empty' }, F.rc ? 'Nobody in this range says on their homepage that they are recruiting. Try a larger range or turn off the filter.' : 'Nobody matches. Try a larger range or fewer filters.')]),
    ...(rows.length > LIMIT ? [el('li', { class: 'fam-empty' }, `Showing the closest ${LIMIT} of ${fmt(rows.length)}.`)] : []));
  }

  /* ---------- about ---------- */
  function renderAbout() {
    const s = G.summary;
    const stat = (v, l) => el('div', { class: 'stat' }, el('b', {}, fmt(v)), el('span', {}, l));
    const a = (href, text) => el('a', { href, target: '_blank', rel: 'noopener' }, text);
    $('#aboutBody').replaceChildren(
      el('h2', {}, 'About'),
      el('p', {}, 'Academic Lineage Family Tree maps who trained whom among computer science faculty at US universities, to help prospective PhD students understand a potential advisor’s academic background and research family. Pick a research interest and institutions to browse professors, then open anyone’s tree: advisors are on the left, students on the right, one generation per band.'),
      el('div', { class: 'stats' }, stat(s.current_faculty ?? s.professors, 'current professors'), stat(G.unis.length, 'institutions'),
        stat(s.current_with_advisor ?? s.with_advisor, 'with a known advisor'), stat(G.nodes.size, 'researchers in the tree')),
      el('h3', {}, 'Where the data comes from'),
      el('ul', {},
        el('li', {}, el('b', {}, 'Faculty list: '), 'names, institutions, areas and homepages from ', a('https://csrankings.org', 'CSRankings'), ' (CC BY-NC-SA 4.0), refreshed November 2026. Only people currently on a US institution’s list are shown as faculty; earlier faculty who have since left (moved abroad, retired, emeritus, industry) still appear in others’ trees as outside researchers, with their new position when known.'),
        el('li', {}, el('b', {}, 'Curated: '), 'advisor names collected by hand from CVs and homepages by our team.'),
        el('li', {}, el('b', {}, 'Wikidata: '), 'doctoral advisor / student statements (CC0), matched by Google Scholar ID, name or institution.'),
        el('li', {}, el('b', {}, 'Wikipedia: '), 'doctoral advisor and doctoral students fields of English Wikipedia infoboxes (CC BY-SA 4.0).'),
        el('li', {}, el('b', {}, 'Homepage / CV: '), 'phrases such as “PhD advisor: …” or “under the supervision of …” found on the professor’s own homepage or CV. Each links to its page.'),
        el('li', {}, el('b', {}, 'dblp (inferred): '), 'for professors with no advisor from the sources above, the co-author who most often appears as senior last author on their papers during their PhD years, using ', a('https://dblp.org', 'dblp'), ' (CC0). Only confident cases are kept; checked against known advisors they are right about 83% of the time.')),
      el('p', {}, 'A curated advisor is marked “unverified” when Wikidata or Wikipedia lists a different advisor for the same person. Photos and job titles are taken from each person’s own homepage; photos are shown directly from that page rather than copied here, so one may disappear if the page changes. Where no photo was found, the avatar shows initials colored by research area; where no title was found, it reads “Faculty”. Favorites are stored only in your browser.'),
      el('p', {}, 'Data can be wrong or incomplete. Always confirm with the professor’s own CV.'));
  }

  /* ---------- routing ---------- */
  const current = { view: null };
  let lastList = '#/search';
  function go(id) { location.hash = '#/p/' + encodeURIComponent(id); }
  function show(view) {
    for (const v of ['home', 'results', 'favorites', 'tree', 'about']) $('#view-' + v).hidden = v !== view;
    for (const link of document.querySelectorAll('[data-nav]')) link.classList.toggle('active', link.dataset.nav === view);
    current.view = view;
    closePanels(); hideTip();
    window.scrollTo(0, 0);
  }
  function route() {
    const h = location.hash || '#/';
    const m = h.match(/^#\/p\/(.+)$/);
    if (m && G.nodes.has(decodeURIComponent(m[1]))) { show('tree'); openTree(decodeURIComponent(m[1])); return; }
    if (h.startsWith('#/search')) {
      const p = new URLSearchParams(h.split('?')[1] || '');
      S.subs = new Set((p.get('r') || '').split(',').map((k) => k.trim()).flatMap((k) => (SUB_LABEL[k] ? [k] : AREAS[k] ? Object.keys(AREAS[k][1]) : [])));
      S.unis = new Set((p.get('i') || '').split(',').map(uniIndex).filter((i) => i != null));
      lastList = h;
      R.shown = PAGE;
      show('results'); renderResults();
      document.title = 'Results · Academic Lineage Family Tree';
      return;
    }
    if (h === '#/favorites') { lastList = h; show('favorites'); renderFavorites(); document.title = 'Favorites · Academic Lineage Family Tree'; return; }
    if (h === '#/about') { show('about'); document.title = 'About · Academic Lineage Family Tree'; return; }
    show('home');
    document.title = 'Academic Lineage Family Tree';
  }

  /* ---------- init ---------- */
  load().then(() => {
    for (const box of document.querySelectorAll('[data-search]')) mountSearch(box, box.dataset.search);
    const s = G.summary;
    $('#heroStats').textContent = `${fmt(s.current_faculty ?? s.professors)} professors · ${G.unis.length} institutions · ${fmt(s.current_with_advisor ?? s.with_advisor)} with a known advisor`;
    renderAbout();
    updateFavCount();
    $('#onlyKnown').addEventListener('change', (e) => { R.onlyKnown = e.target.checked; R.shown = PAGE; renderResults(); });
    $('#onlyAP').addEventListener('change', (e) => { R.onlyAP = e.target.checked; R.shown = PAGE; renderResults(); });
    $('#onlyRC').addEventListener('change', (e) => { R.onlyRC = e.target.checked; R.shown = PAGE; renderResults(); });
    for (const b of document.querySelectorAll('.fam-controls [data-dist]')) b.addEventListener('click', () => { F.dist = +b.dataset.dist; renderFamily(); });
    for (const [id, key] of [['famAP', 'ap'], ['famRC', 'rc'], ['famArea', 'area'], ['famAll', 'all']]) $('#' + id).addEventListener('change', (e) => { F[key] = e.target.checked; renderFamily(); });
    $('#sort').addEventListener('change', (e) => { R.sort = e.target.value; renderResults(); });
    let nt;
    $('#nameFilter').addEventListener('input', (e) => { clearTimeout(nt); nt = setTimeout(() => { R.name = e.target.value; R.shown = PAGE; renderResults(); }, 150); });
    $('#more').addEventListener('click', () => { R.shown += PAGE; renderResults(); });
    $('#backBtn').addEventListener('click', () => { location.hash = lastList; });
    $('#genLeft').addEventListener('click', () => { T.lo -= 1; drawTree(); });
    $('#genRight').addEventListener('click', () => { T.lo += 1; drawTree(); });
    const zoom = (d) => { T.zoom = Math.max(0.6, Math.min(1.6, +(T.zoom + d).toFixed(2))); $('#scaleBox').style.transform = `scale(${T.zoom})`; drawTree(); };
    $('#zoomIn').addEventListener('click', () => zoom(0.15));
    $('#zoomOut').addEventListener('click', () => zoom(-0.15));
    $('#fullBtn').addEventListener('click', () => {
      const shell = $('#treeShell');
      if (document.fullscreenElement) document.exitFullscreen(); else if (shell.requestFullscreen) shell.requestFullscreen();
    });
    document.addEventListener('fullscreenchange', () => setTimeout(drawTree, 60));
    let rt;
    addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if (current.view === 'tree') { T.lo = null; drawTree(); } }, 150); });
    addEventListener('hashchange', route);
    route();
  }).catch((err) => {
    document.querySelector('main').replaceChildren(el('p', { class: 'noscript' }, 'Could not load the data. ' + err.message));
  });
})();
