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
  const AREA_COLOR = { ai: '#5b8a68', systems: '#4f7783', theory: '#9a8350', interdisciplinary: '#7a6c97' };
  const SRC_LABEL = { sheet: 'Curated', wikidata: 'Wikidata', wikipedia: 'Wikipedia', homepage: 'Homepage', cv: 'CV', dblp: 'dblp' };
  const INFER_TIP = 'Inferred from dblp: the most frequent senior last author on this person’s papers during their PhD years. About 83% accurate when checked against known advisors.';
  const PAGE = 30;
  const MAXGEN = 6;

  // 版本号来自 index.html 里 app.js?v=…：每次发布改这个号，浏览器就不会用旧的缓存
  const V = (document.currentScript && new URL(document.currentScript.src).searchParams.get('v')) || '';
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
    // 和 index.html 里 <link rel="preload"> 用同一个地址，浏览器才会复用预加载的结果
    const pre = document.querySelector('link[rel="preload"][as="fetch"]');
    const url = pre ? pre.getAttribute('href') : 'data/graph.json' + (V ? '?v=' + V : '');
    const raw = await (await fetch(url)).json();
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
      if (n.ua) n.usAcad = true;  // 数据里已标明：在美国高校任教，只是不在 CSRankings 名单上
    }
    for (const n of nodes.values()) {
      n.knownAdv = n.adv.some((e) => !e.disputed && !e.inferred);  // 只算有来源记录的导师，dblp 推断的不算
      n.degree = n.adv.length + n.stu.length;
    }
    const profs = [...nodes.values()].filter((n) => !n.ext);
    const subCount = {};
    for (const p of profs) if (!p.former) for (const d of p.d) subCount[d] = (subCount[d] || 0) + 1;
    const uniCount = new Map();
    for (const p of profs) if (!p.former) uniCount.set(p.u, (uniCount.get(p.u) || 0) + 1);
    G = { nodes, profs, unis: raw.unis, summary: raw.summary, subCount, uniCount, hasAS: profs.some((p) => p.as) };
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
      style: n.ext ? null : `--av:${AREA_COLOR[AREA_OF[(n.d || [])[0]]] || '#5b8a68'}`,
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
  // AP 入职年份：n.as = [年份, 是否尚未入职, 原句, 出处]；近几年入职的新 AP 最缺学生
  const apSince = (n) => (isAP(n) && n.as ? (n.as[1] && n.as[0] > new Date().getFullYear() ? `starting ${n.as[0]}` : `since ${n.as[0]}`) : '');
  const isNewAP = (n) => !!(isAP(n) && n.as && n.as[0] >= ADMIT_YEAR - 3);
  const apYearUnknown = (n) => isAP(n) && !n.as;  // 是 AP，但主页上没找到入职年份
  const titleLine = (n, short = false) => { const t = short ? shortTitle(title(n)) : title(n), a = apSince(n); return a ? `${t} ${a}` : t; };
  // 主页上的招生声明：n.rc = [yes/no, 目标年份, 抓取年月, 原句, 出处]
  // 现在申请的是哪一年秋季入学：8 月以后算下一年
  const ADMIT_YEAR = (() => { const d = new Date(); return d.getFullYear() + (d.getMonth() >= 7 ? 1 : 0); })();
  const rcStale = (n) => !!(n.rc && n.rc[1] && n.rc[1] < ADMIT_YEAR);
  // 只算写了今年（或以后）年份的“在招”：抽查里带年份的 20/20 准确，没写年份的只有约一成能确认是今年
  const rcFlags = (n) => (n.rc && n.rc[5]) || {};
  const recruiting = (n) => !!(n.rc && n.rc[0] === 'yes' && n.rc[1] && n.rc[1] >= ADMIT_YEAR);
  // 没写年份的说法（"I am looking for students"）可能是旧的，标得弱一些
  const rcUndated = (n) => !!(n.rc && n.rc[0] === 'yes' && !n.rc[1]);
  // 没写年份、也没提 PhD，或页面已经很久没更新、或说的是别的学校：不挂标签，只在详情里提一句
  const rcWeak = (n) => rcUndated(n) && (rcFlags(n).phd === false || rcFlags(n).old || !!rcFlags(n).inst);
  // 第二档：没写年份、但说的是招 PhD 的（徽章写 “Open to students”）。筛选里作为 “+ open to students (no year)” 选项
  const openToStudents = (n) => rcUndated(n) && !rcWeak(n);
  const rcMatch = (n, open) => recruiting(n) || (open && openToStudents(n));
  // 两档各有多少人：“2 recruiting for 2027 · 8 more open to students”
  const rcCounts = (list) => ({ rc: list.filter(recruiting).length, open: list.filter(openToStudents).length });
  const rcSummary = ({ rc, open }) => `${fmt(rc)} recruiting for ${ADMIT_YEAR}` + (open ? ` · ${fmt(open)} more open to students` : '');
  // 收藏表格和导出的 CSV 用同一套说法
  const rcStatus = (n) => (!n.rc ? 'No statement found' : rcWeak(n) ? 'Unclear statement (see details)' : rcText(n));
  const rcLabel = (n) => (rcStale(n) ? `Recruited for ${n.rc[1]} · may be outdated` : n.rc[1] ? `Recruiting for ${n.rc[1]}` : 'Open to students');
  const rcBadge = (n, cls = '') => {
    if (!n.rc || (n.rc[0] === 'no' && rcStale(n)) || rcWeak(n)) return null;
    const no = n.rc[0] === 'no';
    return el('span', { class: `rc-badge ${cls} ${no || rcStale(n) ? 'stale' : ''} ${rcUndated(n) ? 'soft' : ''}`,
      title: `${rcUndated(n) ? 'No year given, so it may be out of date. ' : ''}Homepage (seen ${n.rc[2]}): “${n.rc[3]}”` },
      no ? (n.rc[1] ? `Not recruiting for ${n.rc[1]}` : 'Not taking students') : rcLabel(n));
  };
  // 名单外的人：有去向写去向，导师主页列为当前学生的写 Student
  const outsideSub = (n) => (n.af ? shortAff(n.af) : n.st ? 'PhD student' : 'Position unknown');
  const shortAff = (s) => {
    // 去掉“Thesis: …”这类论文题目，和开头的 now at / currently
    s = s.replace(/\s*(?:Thesis|Dissertation)\s*:.*$/i, '').replace(/^(?:now|currently)\s+(?:at\s+)?/i, '').trim() || s;
    let t = (s.length > 34 ? s.split(/,\s*/)[0] : s);
    if (t.length > 40) t = t.slice(0, 40).replace(/\s+\S*$/, '') + '…';
    return (t.split('(').length > t.split(')').length) ? t.replace(/\s*\([^)]*$/, '') : t;  // 别留半个括号
  };
  const interest = (n, k = 2) => n.d.slice(0, k).map((d) => SUB_LABEL[d] || d).join(', ');

  /* ---------- shared search filters ---------- */
  const S = { subs: new Set(), unis: new Set() };
  const cards = [];

  let openPanel = null;
  function closePanels(refocus = false) {
    const trigger = openPanel && openPanel.previousElementSibling;
    if (openPanel) { openPanel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); openPanel = null; }
    for (const c of cards) c.sync();  // 也用来在换页时刷新搜索框里的标签
    if (trigger && refocus) trigger.focus();
  }
  // 点“Search”时不在 mousedown 里先收起面板：收起会重画搜索框，按钮的 click 可能就丢了；它自己的 click 会收起面板再搜索
  document.addEventListener('mousedown', (e) => { if (openPanel && !openPanel.parentElement.contains(e.target) && !e.target.closest('.go')) closePanels(); });
  const coarsePointer = matchMedia('(pointer: coarse)');
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || $('#fixDialog').open) return;  // 纠错表单开着时由对话框自己处理 Esc
    if (openPanel) { closePanels(true); return; }
    if (current.view === 'tree') closeDrawer();
  });

  let ddSeq = 0;
  function dropdown(icon, onOpen, labelId) {
    const id = 'dd' + (++ddSeq);
    const trigger = el('button', { class: 'dd-trigger', type: 'button', id: id + 't', 'aria-controls': id, 'aria-expanded': 'false',
      'aria-labelledby': labelId ? `${labelId} ${id}t` : null });
    const panel = el('div', { class: 'dd-panel', id, hidden: true });
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
    const box = el('div', { class: 'dd' }, trigger, panel);
    // Tab 键离开下拉框时收起（relatedTarget 为空的情况交给上面的 mousedown 处理，Safari 点复选框不会给焦点）
    box.addEventListener('focusout', (e) => { if (openPanel === panel && e.relatedTarget && !box.contains(e.relatedTarget)) closePanels(); });
    return { box, panel, setChips };
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
    // 顶部输入框：打几个字直接筛出匹配的子方向（不用先展开大类），如 “vis” → Computer vision、Visualization
    const q = el('input', { type: 'search', placeholder: 'Type to filter, e.g. vis', 'aria-label': 'Filter research areas' });
    const allRow = el('label', { class: 'row area' }, all, 'ALL');
    const none = el('p', { class: 'dd-none', hidden: true }, 'No matching area.');
    panel.replaceChildren(el('div', { class: 'uni-search' }, q), allRow);
    const groups = [];
    for (const [, [lab, subs]] of Object.entries(AREAS)) {
      const ks = Object.keys(subs).filter((k) => G.subCount[k]);
      const acb = el('input', { type: 'checkbox' });
      const subRows = ks.map((k) => {
        const cb = el('input', { type: 'checkbox' });
        cb.addEventListener('change', () => { cb.checked ? S.subs.add(k) : S.subs.delete(k); sync(); });
        rows.push(() => { cb.checked = S.subs.has(k); });
        const row = el('label', { class: 'row sub', hidden: true }, cb, subs[k], el('small', {}, fmt(G.subCount[k])));
        row.key = fold(`${subs[k]} ${SUB_SHORT[k]} ${k}`);
        return row;
      });
      const exp = el('button', { class: 'exp', type: 'button', 'aria-label': `Expand ${lab}`, 'aria-expanded': 'false' }, '+');
      let expanded = false;
      exp.addEventListener('click', (e) => {
        e.preventDefault();
        expanded = !expanded;
        for (const r of subRows) r.hidden = !expanded;
        exp.textContent = expanded ? '−' : '+'; exp.setAttribute('aria-expanded', String(expanded));
      });
      acb.addEventListener('change', () => { for (const k of ks) acb.checked ? S.subs.add(k) : S.subs.delete(k); sync(); });
      rows.push(() => {
        const on = ks.filter((k) => S.subs.has(k)).length;
        acb.checked = on === ks.length; acb.indeterminate = on > 0 && on < ks.length;
      });
      const head = el('div', { class: 'row area' }, exp, el('label', { class: 'area-label' }, acb, lab));
      groups.push({ head, subRows, key: fold(lab), isOpen: () => expanded });
      panel.append(head, ...subRows);
    }
    panel.append(none);
    q.addEventListener('input', () => {
      const t = fold(q.value);
      allRow.hidden = !!t;
      let any = false;
      for (const g of groups) {
        const whole = t && g.key.includes(t);
        let shown = 0;
        for (const r of g.subRows) { r.hidden = t ? !(whole || r.key.includes(t)) : !g.isOpen(); if (!r.hidden) shown++; }
        g.head.hidden = !!t && !shown;
        any = any || !t || shown > 0;
      }
      none.hidden = any;
    });
    sync();
    if (!coarsePointer.matches) setTimeout(() => q.focus(), 0);  // 手机上不自动弹出键盘挡住列表
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
    if (!coarsePointer.matches) setTimeout(() => q.focus(), 0);
  }

  function mountSearch(box, variant) {
    const labA = `lab-${variant}-area`, labU = `lab-${variant}-uni`;
    const area = dropdown('icon_book.png', (panel) => buildAreaPanel(panel, () => syncChips()), labA);
    const uni = dropdown('icon_institution.png', (panel) => buildUniPanel(panel, () => syncChips()), labU);
    const syncChips = () => {
      area.setChips(chipList(areaLabels()), 'Choose a tag.', openPanel === area.panel);
      const u = [...S.unis].map((i) => G.unis[i][1] || G.unis[i][0]);
      uni.setChips(u.length ? chipList(u) : [el('span', { class: 'chip' }, 'All')], '', openPanel === uni.panel);
    };
    const go = el('button', { class: 'go', type: 'button', 'aria-label': 'Search' }, el('img', { src: 'assets/icons/icon_search.png', alt: '' }), 'Search');
    go.addEventListener('click', () => { closePanels(); location.hash = searchHash(); });
    box.append(el('div', { class: 'search-row' },
      el('div', { class: 'field' }, el('span', { class: 'lab', id: labA }, 'Research Interest'), area.box),
      el('div', { class: 'field' }, el('span', { class: 'lab', id: labU }, 'Institution'), uni.box),
      go));
    if (variant === 'home') box.append(nameSearch());
    cards.push({ sync: syncChips });
    syncChips();
  }

  // 姓名搜索框：首页用来直接跳到某人的树；“两人关系”页用 keep 模式，选中后框里保留名字
  // 拼写容错：限定上限的编辑距离（含相邻字母对调，如 chne → chen），超过上限就提前放弃
  function editDist(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    let p2 = null, p1 = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      let best = i;
      for (let j = 1; j <= b.length; j++) {
        let v = Math.min(p1[j] + 1, cur[j - 1] + 1, p1[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (p2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, p2[j - 2] + 1);
        cur.push(v); if (v < best) best = v;
      }
      if (best > max) return max + 1;
      p2 = p1; p1 = cur;
    }
    return p1[b.length];
  }
  // 一个查询词和名字里各个词的最小距离：可以是整词，也可以是同样长度的词头（还没打完时）
  const tokDist = (w, toks, max) => {
    let best = max + 1;
    for (const t of toks) {
      if (t.length < 2) continue;
      best = Math.min(best, editDist(w, t, max), t.length > w.length ? editDist(w, t.slice(0, w.length), max) : max + 1);
      if (!best) break;
    }
    return best;
  };
  const whereLabel = (n) => (n.ext ? (n.usAcad && n.af ? shortAff(n.af) + ' · not on CSRankings' : 'other researcher') : n.uni + (n.former ? ' (former)' : ''));
  // 按姓名找人：每个词都要出现；词头匹配排在中间包含的前面。都找不到时按拼写距离给 “Did you mean …”
  function findPeople(q, limit = 8) {
    const t = fold(q);
    if (t.length < 2) return { hits: [], fuzzy: false };
    const words = t.split(' ');
    const out = [];
    for (const n of G.nodes.values()) {
      if (!words.every((w) => n.f.includes(w))) continue;
      const toks = n.toks || (n.toks = n.f.split(' '));
      const starts = words.filter((w) => toks.some((x) => x.startsWith(w))).length;
      const score = (n.f.startsWith(t) ? 0 : 1) + (words.length - starts) * 2 + (n.ext ? 3 : 0) + (n.former ? 1 : 0);
      out.push({ n, score });
    }
    if (out.length) {
      out.sort((a, b) => a.score - b.score || b.n.degree - a.n.degree);
      return { hits: out.slice(0, limit).map((x) => x.n), fuzzy: false };
    }
    // 拼写容错：短词只允许 1 处错，4 个字母以上允许 2 处；1–2 个字母的词（缩写）必须是词头
    const fz = [];
    for (const n of G.nodes.values()) {
      const toks = n.toks || (n.toks = n.f.split(' '));
      let total = 0;
      for (const w of words) {
        if (w.length <= 2) { if (!toks.some((x) => x.startsWith(w))) { total = -1; break; } continue; }
        const d = tokDist(w, toks, w.length >= 4 ? 2 : 1);
        if (d > (w.length >= 4 ? 2 : 1)) { total = -1; break; }
        total += d;
      }
      if (total >= 0) fz.push({ n, score: total * 2 + (n.ext ? 3 : 0) + (n.former ? 1 : 0) });
    }
    fz.sort((a, b) => a.score - b.score || b.n.degree - a.n.degree);
    return { hits: fz.slice(0, 3).map((x) => x.n), fuzzy: true };
  }

  let sgSeq = 0;
  const NAME_PH = ['Or jump to a professor by name, e.g. Jeffrey Heer', 'Jump to a professor by name'];
  const narrow = matchMedia('(max-width: 760px)');
  function nameSearch(o = {}) {
    const sid = 'sg' + (++sgSeq);
    // 手机上占位文字短一点，免得被截成 “…by name, e.”
    const ph = () => o.placeholder || NAME_PH[narrow.matches ? 1 : 0];
    const input = el('input', { type: 'search', id: o.id || null, placeholder: ph(), autocomplete: 'off',
      'aria-label': o.label || 'Search professors by name', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': sid });
    if (!o.placeholder) narrow.addEventListener('change', () => { input.placeholder = ph(); });
    const box = el('ul', { class: 'suggest', id: sid, role: 'listbox', hidden: true });
    let hits = [], sel = -1, picked = null, fuzzy = false;
    const render = () => {
      box.replaceChildren(...(fuzzy && hits.length ? [el('li', { class: 'dym', role: 'presentation' }, 'No exact match. Did you mean:')] : []),
        ...hits.map((n, i) => el('li', {
          role: 'option', id: `${sid}-${i}`, 'aria-selected': i === sel ? 'true' : 'false',
          onmousedown: (e) => { e.preventDefault(); pick(n); },
        }, el('span', {}, n.n), el('small', {}, whereLabel(n)))));
      if (!hits.length && fold(input.value).length >= 2) box.replaceChildren(el('li', { class: 'none', role: 'option', 'aria-disabled': 'true' }, 'No one found. Try a last name or another spelling.'));
      box.hidden = (!hits.length && fold(input.value).length < 2) || (!hits.length && !!picked && input.value === picked.n);
      input.setAttribute('aria-expanded', String(!box.hidden));
      if (sel >= 0 && hits[sel]) input.setAttribute('aria-activedescendant', `${sid}-${sel}`); else input.removeAttribute('aria-activedescendant');
    };
    const pick = (n) => {
      clearTimeout(st); st = null;
      hits = [];
      if (o.keep) { picked = n; input.value = n.n; render(); o.onPick(n); return; }
      input.value = ''; render(); (o.onPick || ((x) => go(x.id)))(n);
    };
    // 每次按键都扫一遍上万人太费；停顿 100 ms 再搜（按方向键 / 回车时立即搜完）
    let st = null;
    const search = () => {
      st = null;
      ({ hits, fuzzy } = findPeople(input.value));
      sel = -1;  // 不预先选中：第一次按 ↓ 就落在第一项
      render();
    };
    input.addEventListener('input', () => {
      if (o.keep && picked && !input.value) { picked = null; o.onPick(null); }  // 点了搜索框里的 ×：清空这一栏
      clearTimeout(st);
      if (fold(input.value).length < 2) { search(); return; }
      st = setTimeout(search, 100);
    });
    input.addEventListener('keydown', (e) => {
      if (st && ['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) { clearTimeout(st); search(); }
      if (e.key === 'ArrowDown') { if (hits.length) sel = Math.min(hits.length - 1, sel + 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(-1, sel - 1); render(); e.preventDefault(); }
      // 回车选高亮的那一项；没有高亮时，只有精确匹配才直接打开第一个（拼写猜测要用户自己选）
      else if (e.key === 'Enter' && (hits[sel] || (!fuzzy && hits[0]))) { pick(hits[sel] || hits[0]); e.preventDefault(); }
      else if (e.key === 'Escape' && !box.hidden) { hits = []; box.hidden = true; input.setAttribute('aria-expanded', 'false'); e.stopPropagation(); }
    });
    input.addEventListener('blur', () => setTimeout(() => {
      box.hidden = true; input.setAttribute('aria-expanded', 'false');
      if (o.keep && picked && input.value !== picked.n) input.value = picked.n;  // 没选新的人：恢复原来的名字
    }, 120));
    const wrap = el('div', { class: 'name-row' }, el('span', { class: 'glass' }), input, box);
    // 外部设置当前选中的人（从网址恢复、交换 A/B 时用）
    wrap.input = input;
    wrap.set = (n) => { clearTimeout(st); st = null; picked = n || null; input.value = n ? n.n : ''; hits = []; fuzzy = false; render(); };
    return wrap;
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
    if (R.onlyKnown) p.push('known=1');
    if (R.onlyAP) p.push('ap=1');
    if (R.onlyRC) p.push('recruiting=1');
    if (R.onlyRC && R.rcOpen) p.push('open=1');
    if (R.sort !== 'lineage') p.push('sort=' + R.sort);
    const q = p.join('&');
    return '#/search' + (q ? '?' + q : '');
  };

  /* ---------- results ---------- */
  const R = { onlyKnown: false, onlyAP: false, onlyRC: false, rcOpen: false, sort: 'lineage', shown: PAGE, name: '' };
  const SORTS = ['lineage', 'recruiting', 'name', 'uni'];
  const rcRank = (n) => (recruiting(n) ? 2 : openToStudents(n) ? 1 : 0);
  function filtered() {
    const t = fold(R.name);
    // 先算除“招生”以外的筛选，用来给两档招生各数一个数
    const base = G.profs.filter((p) =>
      (!S.subs.size || p.d.some((d) => S.subs.has(d))) &&
      (!S.unis.size || S.unis.has(p.u)) &&
      (!R.onlyKnown || p.knownAdv) &&
      (!R.onlyAP || isAP(p)) &&
      !p.former &&
      (!t || p.f.includes(t)));
    R.counts = rcCounts(base);
    const list = R.onlyRC ? base.filter((p) => rcMatch(p, R.rcOpen)) : base;
    const by = {
      lineage: (a, b) => b.degree - a.degree || a.n.localeCompare(b.n),
      recruiting: (a, b) => rcRank(b) - rcRank(a) || b.degree - a.degree || a.n.localeCompare(b.n),
      name: (a, b) => a.n.localeCompare(b.n),
      uni: (a, b) => a.uni.localeCompare(b.uni) || a.n.localeCompare(b.n),
    }[R.sort] || ((a, b) => b.degree - a.degree);
    return list.sort(by);
  }
  // 两个招生开关上写人数；第二个（不写年份的）打开时自动打开第一个
  function syncRcToggles() {
    $('#onlyRC').checked = R.onlyRC; $('#onlyRCOpen').checked = R.onlyRC && R.rcOpen;
    if (R.counts) {
      $('#onlyRCLabel').textContent = `Recruiting for ${ADMIT_YEAR} (${fmt(R.counts.rc)})`;
      $('#onlyRCOpenLabel').textContent = `+ ${fmt(R.counts.open)} open to students (no year)`;
    }
  }

  function card(p) {
    const nA = p.adv.filter((e) => !e.disputed).length, nS = p.stu.length;
    const star = el('button', { class: `corner ${favs.has(p.id) ? 'on' : ''}`, type: 'button', 'aria-pressed': favs.has(p.id) ? 'true' : 'false', 'aria-label': `Star ${p.n}` }, el('span', { class: 'star' }));
    star.addEventListener('click', (e) => {
      e.stopPropagation(); toggleFav(p.id);
      star.classList.toggle('on', favs.has(p.id)); star.setAttribute('aria-pressed', favs.has(p.id) ? 'true' : 'false');
      if (current.view === 'favorites') renderFavorites();
    });
    // 整张卡片可点：名字是真正的链接，用 ::after 铺满卡片（Ctrl / 中键点击可在新标签页打开）
    const link = el('a', { class: 'card-link', href: '#/p/' + encodeURIComponent(p.id), onclick: () => { if (current.view === 'results') R.scroll = scrollY; } }, p.n);
    return el('li', { class: 'card' },
      avatar(p, true), star,
      el('div', { class: 'card-body' },
        el('div', { class: 'card-name' }, link),
        el('div', { class: 'card-meta' },
          el('div', { class: `c-title ${isAP(p) ? 'is-ap' : ''}` }, titleLine(p)),
          el('div', {}, p.uni),
          p.nf ? el('div', {}, el('span', { class: 'nf-badge', title: 'Joined recently; not yet on the CSRankings faculty list. Added from the university’s announcement.' }, 'New faculty')) : null),
        p.d.length ? el('div', { class: 'card-tags' }, p.d.slice(0, 3).map((x) => el('span', { class: 'tag' }, SUB_SHORT[x] || x))) : null,
        el('div', { class: 'card-foot' },
          nA ? el('span', { title: p.knownAdv ? null : 'Inferred from dblp co-authorship, not recorded in a source' }, el('b', {}, nA), ` ${p.knownAdv ? '' : 'inferred '}advisor${nA > 1 ? 's' : ''}`) : el('span', {}, 'Advisor unknown'),
          nS ? el('span', {}, el('b', {}, nS), ` student${nS > 1 ? 's' : ''}`) : null,
          rcBadge(p))));
  }

  function renderResults() {
    const list = R.list = filtered();
    syncRcToggles();
    $('#resCount').replaceChildren(`${fmt(list.length)} professor${list.length === 1 ? '' : 's'}`, el('small', {}, areaLabels().join(', ') || 'All areas'));
    const ul = $('#cards');
    ul.replaceChildren(...list.slice(0, R.shown).map(card));
    if (!list.length) ul.append(el('li', { class: 'empty' }, R.onlyRC && !R.rcOpen && R.counts.open
      ? `Nobody here gives ${ADMIT_YEAR} on their homepage. Turn on “+ open to students (no year)” to see ${fmt(R.counts.open)} more.`
      : 'No professors match. Try fewer tags or another institution.'));
    $('#more').hidden = list.length <= R.shown;
  }
  // “Show more”：只追加下一页，不重画已有的卡片（照片不重新加载、焦点不丢）
  function showMore() {
    const start = R.shown;
    R.shown += PAGE;
    const add = R.list.slice(start, R.shown).map(card);
    $('#cards').append(...add);
    $('#more').hidden = R.list.length <= R.shown;
    if (add[0]) $('.card-link', add[0]).focus();
  }

  /* ---------- favorites = my application list ---------- */
  const NOTE_KEY = 'alt-notes';
  let notes = {};
  try { notes = JSON.parse(localStorage.getItem(NOTE_KEY) || '{}') || {}; } catch (e) { notes = {}; }
  const saveNotes = () => { try { localStorage.setItem(NOTE_KEY, JSON.stringify(notes)); } catch (e) { /* storage unavailable */ } };
  const favList = () => [...favs].map((id) => G.nodes.get(id)).filter((n) => n && !n.ext);
  const rcText = (n) => (!n.rc ? '' : n.rc[0] === 'no' ? (n.rc[1] ? `Not recruiting for ${n.rc[1]}` : 'Not taking students') : rcLabel(n));

  const cell = (cls, label, ...kids) => el('td', { class: cls, 'data-label': label }, el('div', { class: 'f-cell' }, ...kids));
  let favView = 'cards';
  try { favView = localStorage.getItem('alt-fav-view') === 'table' ? 'table' : 'cards'; } catch (e) { /* storage unavailable */ }
  function renderFavorites() {
    const list = favList();
    // 默认卡片；“Table & notes”是表格视图，可以写备注、导出
    for (const b of document.querySelectorAll('[data-favview]')) { b.classList.toggle('on', b.dataset.favview === favView); b.setAttribute('aria-pressed', String(b.dataset.favview === favView)); }
    $('#favCards').hidden = favView !== 'cards';
    $('#favTableWrap').hidden = favView !== 'table';
    $('#favCards').replaceChildren(...list.map(card));
    if (!list.length) $('#favCards').append(el('li', { class: 'empty' }, 'No favorites yet. Use the star on a professor’s card or in the tree to keep them here.'));
    $('#favCount2').textContent = list.length ? `${list.length} professor${list.length > 1 ? 's' : ''} · ${rcSummary(rcCounts(list))}` : '';
    $('#favExport').disabled = !list.length;
    const head = el('tr', {}, ['Professor', 'Position', 'Recruiting', 'Areas', 'Links', 'My notes', ''].map((t) => el('th', { scope: 'col' }, t || el('span', { class: 'sr-only' }, 'Remove'))));
    const rows = list.map((n) => {
      const note = el('textarea', { rows: 1, placeholder: 'Add a note…', 'aria-label': `Notes on ${n.n}` });
      note.value = notes[n.id] || '';
      note.addEventListener('input', () => { if (note.value.trim()) notes[n.id] = note.value; else delete notes[n.id]; saveNotes(); });
      const rm = el('button', { class: 'fav-rm', type: 'button', title: 'Remove from list', 'aria-label': `Remove ${n.n}` }, '×');
      rm.addEventListener('click', () => { toggleFav(n.id); renderFavorites(); });
      const ln = (href, t) => (href ? el('a', { href, target: '_blank', rel: 'noopener' }, t) : null);
      // 手机上每行变成一张卡片（见 style.css），data-label 给各格加上小标题
      return el('tr', {},
        el('td', { class: 'f-who' }, el('a', { href: '#/p/' + encodeURIComponent(n.id), class: 'f-person' }, avatar(n, true),
          el('span', {}, el('b', {}, n.n), el('small', {}, n.uni)))),
        cell(`f-pos ${isAP(n) ? 'is-ap' : ''}`, 'Position', titleLine(n)),
        cell('f-rc', 'Recruiting', rcBadge(n) || el('span', { class: 'muted' }, rcStatus(n))),
        cell('f-areas', 'Areas', n.d.slice(0, 3).map((x) => el('span', { class: 'tag' }, SUB_SHORT[x] || x))),
        cell('f-links', 'Links', ln(n.h, 'Homepage'), ln(n.g, 'Scholar')),
        cell('f-note', 'My notes', note),
        el('td', { class: 'f-rm' }, rm));
    });
    $('#favTable').replaceChildren(el('thead', {}, head), el('tbody', {}, rows.length ? rows
      : [el('tr', {}, el('td', { class: 'empty', colspan: 7 }, 'Your list is empty. Star professors on a result card or in a tree’s details panel to collect them here.'))]));
  }

  function exportFavorites() {
    const cols = ['Name', 'Title', 'AP since', 'Institution', 'Areas', 'Advisor(s)', 'Recruiting', 'Recruiting statement', 'Statement seen', 'Statement source', 'Homepage', 'Google Scholar', 'My notes', 'Family tree'];
    const base = location.href.split('#')[0];
    const advisors = (n) => n.adv.filter((e) => !e.disputed).map((e) => G.nodes.get(e.a).n + (e.inferred ? ' (inferred)' : e.t === 'postdoc' ? ' (postdoc)' : '')).join('; ');
    const rows = favList().map((n) => [n.n, title(n), isAP(n) && n.as ? n.as[0] : '', n.uni, n.d.map((x) => SUB_LABEL[x] || x).join('; '), advisors(n),
      rcStatus(n), n.rc ? n.rc[3] : '', n.rc ? n.rc[2] : '', n.rc ? n.rc[4] : '', n.h || '', n.g || '', notes[n.id] || '', base + '#/p/' + encodeURIComponent(n.id)]);
    // 防公式注入：以 = + - @（或制表符、回车）开头的格子前面加 '，Excel 就当文字而不是公式
    const esc = (v) => { let t = String(v ?? ''); if (/^[=+\-@\t\r]/.test(t)) t = "'" + t; return /[",\n\r]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
    const csv = '﻿' + [cols, ...rows].map((r) => r.map(esc).join(',')).join('\r\n');
    const a = el('a', { href: URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })), download: 'phd-advisor-list.csv' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /* ---------- tree (generation bands) ---------- */
  const T = { root: null, focus: { up: [], down: [] }, page: {}, lo: null, zoom: 1, selected: null, path: null, pathEnd: null };
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
    const role = (n.ext ? (n.usAcad ? 'fac' : 'out') : isAP(n) ? 'fac ap' : 'fac') + (recruiting(n) ? ' rc' : '');
    const longName = n.n.split(/\s+/).some((w) => w.length > 11) ? ' longname' : '';
    const b = el('button', { class: `tnode ${role}${longName}${n.nf ? ' nf' : ''} ${gen === 0 ? 'root' : ''} ${isFocus ? 'focus' : ''} ${T.path && T.path.has(it.id) ? 'on-path' : ''}`, type: 'button', 'data-id': it.id, 'data-gen': gen,
      tabindex: -1, 'aria-current': T.selected === it.id ? 'true' : null },
      avatar(n),
      el('span', { class: 't-name' }, n.n),
      !n.ext && n.ti ? el('span', { class: 't-title' }, titleLine(n, true)) : null,
      el('span', { class: 't-sub' }, n.ext ? outsideSub(n) : n.uniShort),
      gen === 0 && !n.ext ? el('span', { class: 't-dir' }, n.d[0] ? SUB_LABEL[n.d[0]] : '') : null,
      rcBadge(n, 't-rc'),
      badge);
    b.addEventListener('click', () => selectNode(it.id, gen));
    b.addEventListener('dblclick', () => { if (gen !== 0) go(it.id); });
    // 只给鼠标显示悬停提示：触屏上点一下会触发 mousemove 却没有 mouseleave，提示会一直挂着
    b.addEventListener('pointermove', (ev) => { if (ev.pointerType === 'mouse') showTip(ev, n, e); });
    b.addEventListener('pointerleave', hideTip);
    return b;
  }

  // 重画前记下焦点在树上的哪个节点 / 翻页按钮，重画后放回去（键盘用户不会被甩回页面开头）
  function treeFocus() {
    const a = document.activeElement;
    if (!a || !$('#bands').contains(a)) return null;
    if (a.classList.contains('tnode')) return { id: a.dataset.id, gen: a.dataset.gen };
    if (a.dataset.pg) return { gen: a.dataset.gen, pg: a.dataset.pg };
    return null;
  }
  function restoreFocus(f) {
    const bands = $('#bands');
    let t = null;
    if (f && f.id != null) {
      t = $(`.tnode[data-gen="${f.gen}"][data-id="${CSS.escape(f.id)}"]`, bands) || $(`.tnode[data-gen="${f.gen}"]`, bands) || $('.tnode.root', bands);
    } else if (f && f.pg) {
      const other = f.pg === 'prev' ? 'next' : 'prev';
      t = [f.pg, other].map((p) => $(`.pager button[data-gen="${f.gen}"][data-pg="${p}"]`, bands)).find((b) => b && !b.disabled) || null;
      if (!t) t = $(`.tnode[data-gen="${f.gen}"]`, bands);
    }
    // 方向键在节点之间移动：整棵树只有一个节点在 Tab 顺序里（选中的人，否则中心人物）
    const sel = T.selected != null && $(`.tnode[aria-current="true"]`, bands);
    rove((t && t.classList.contains('tnode') ? t : null) || sel || $('.tnode.root', bands) || $('.tnode', bands));
    if (t) t.focus({ preventScroll: true });
  }
  function rove(node) {
    if (!node) return;
    for (const b of $('#bands').querySelectorAll('.tnode[tabindex="0"]')) b.tabIndex = -1;
    node.tabIndex = 0;
  }
  // 方向键：上下 = 同一代里换人，左右 = 导师 / 学生那一代（到边上时自动翻过去）
  function treeKey(e) {
    const cur = e.target.closest && e.target.closest('.tnode');
    if (!cur || e.altKey || e.ctrlKey || e.metaKey) return;
    const bandsEl = $('#bands');
    const list = (band) => [...band.querySelectorAll('.tnode')];
    const gen = cur.dataset.gen;
    // 翻这一代的页（↓ 到底 / ↑ 到顶 / PageDown / PageUp），翻完把焦点放到新一页的第一个或最后一个人
    const turnPage = (pg, last) => {
      const b = $(`.pager button[data-gen="${gen}"][data-pg="${pg}"]`, bandsEl);
      if (!b || b.disabled) return false;
      b.click();
      const l = [...bandsEl.querySelectorAll(`.tnode[data-gen="${gen}"]`)];
      const t = last ? l[l.length - 1] : l[0];
      if (t) { rove(t); t.focus({ preventScroll: true }); }
      return true;
    };
    let next = null;
    if ((e.key === 'c' && !e.shiftKey) || (e.key === 'Enter' && e.shiftKey)) {
      // 以这个人为中心重新画树
      e.preventDefault();
      if (gen !== '0') go(cur.dataset.id);
      return;
    } else if (e.key === 'PageDown' || e.key === 'PageUp') {
      e.preventDefault();
      turnPage(e.key === 'PageDown' ? 'next' : 'prev', false);
      return;
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      const l = list(cur.closest('.band')), i = l.indexOf(cur), down = e.key === 'ArrowDown';
      next = l[i + (down ? 1 : -1)];
      if (!next) { e.preventDefault(); turnPage(down ? 'next' : 'prev', !down); return; }
    } else if (e.key === 'Home' || e.key === 'End') {
      const l = list(cur.closest('.band'));
      next = e.key === 'Home' ? l[0] : l[l.length - 1];
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const d = e.key === 'ArrowRight' ? 1 : -1;
      const g = +cur.dataset.gen + d;
      if (!$(`.tnode[data-gen="${g}"]`, bandsEl) && !$(d > 0 ? '#genRight' : '#genLeft').disabled) {
        const id = cur.dataset.id;
        T.lo += d; drawTree();
        next = $(`.tnode[data-gen="${gen}"][data-id="${CSS.escape(id)}"]`, bandsEl);
        if (next) next.focus({ preventScroll: true });
      }
      const cands = [...bandsEl.querySelectorAll(`.tnode[data-gen="${g}"]`)];
      if (cands.length) {
        const ref = $(`.tnode[data-gen="${cur.dataset.gen}"][data-id="${CSS.escape(cur.dataset.id)}"]`, bandsEl) || cur;
        const y = ref.offsetTop + ref.offsetHeight / 2;
        next = cands.find((c) => c.classList.contains('focus') || c.classList.contains('root'))
          || cands.reduce((b, c) => (Math.abs(c.offsetTop + c.offsetHeight / 2 - y) < Math.abs(b.offsetTop + b.offsetHeight / 2 - y) ? c : b));
      } else next = null;
    } else return;
    e.preventDefault();
    if (next) { rove(next); next.focus({ preventScroll: true }); }
  }

  function drawTree() {
    if (!T.root) return;
    hideTip();
    const keep = treeFocus();
    const { n, av, perPage } = layoutParams();
    T.n = n;
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
    if (T.pathEnd != null) {  // 路径末端那一代要在可见范围内
      const g = T.pathEnd;
      if (g < T.lo) T.lo = g; else if (g > T.lo + n - 1) T.lo = g - n + 1;
      T.pathEnd = null;
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
      if (label) {
        const cnt = col && col.items.length > 1 ? ` · ${col.items.length}` : '';
        const k = Math.abs(g), short = k === 1 ? label : `${g < 0 ? 'Adv.' : 'Stu.'} +${k - 1}`;
        band.append(el('div', { class: 'gen-label', title: genTitle(g, col) }, el('span', { class: 'gl-long' }, label + cnt), el('span', { class: 'gl-short' }, short + cnt)));
      }
      if (col) {
        const dir = g > 0 ? 'down' : 'up';
        const f = g === 0 ? null : T.focus[dir][Math.abs(g) - 1];
        const items = g === 0 ? col.items : col.items.slice(col.page * perPage, (col.page + 1) * perPage);
        band.append(el('div', { class: 'stack' }, items.map((it) => tnode(it, g, it.id === f && cols.has(g + Math.sign(g))))));
        if (col.pages > 1) {
          const what = genLabel(g, col).toLowerCase() || 'people';
          const prev = el('button', { type: 'button', 'aria-label': `Previous page of ${what}`, 'data-gen': g, 'data-pg': 'prev' }, '‹');
          const next = el('button', { type: 'button', 'aria-label': `Next page of ${what}`, 'data-gen': g, 'data-pg': 'next' }, '›');
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
    restoreFocus(keep);
    requestAnimationFrame(drawLinks);
  }

  function drawLinks() {
    const svg = $('#links'), box = $('#scaleBox');
    // 用布局位置（offsetLeft/Top 一路加到 #scaleBox），不受缩放动画进行到一半的 transform 影响
    const pos = (node) => {
      const av = node.querySelector('.avatar');
      let x = 0, y = 0;
      for (let p = av; p && p !== box; p = p.offsetParent) { x += p.offsetLeft; y += p.offsetTop; }
      return { l: x, r: x + av.offsetWidth, y: y + av.offsetHeight / 2 };
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
      const cls = (!e ? '' : e.disputed ? 'disputed' : e.inferred ? 'inferred' : e.t === 'postdoc' ? 'postdoc' : '') +
        (T.path && T.path.has(node.dataset.id) && T.path.has(parentId) ? ' on-path' : '');
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
    T.path = null;
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

  /* ---------- evidence for one advisor–student link ---------- */
  const SRC_INFO = {
    sheet: 'Collected by hand from CVs and homepages by our team. No link was recorded.',
    cv: 'Stated on a CV.',
    homepage: 'Stated on a homepage, for example “PhD advisor: …” or a lab’s list of students.',
    wikidata: 'A “doctoral advisor” or “doctoral student” statement on Wikidata.',
    wikipedia: 'The doctoral advisor or doctoral students field of an English Wikipedia infobox.',
    dblp: INFER_TIP,
  };
  const DBLP_AGREES = 'Also matches our dblp check: this advisor is the most frequent senior last author on the student’s papers during their PhD years.';
  const SRC_NAME = { ...SRC_LABEL, sheet: 'Curated sheet', dblp: 'dblp (inferred)' };
  // 一条关系只存了一个链接：按域名判断它属于哪个来源，判断不出来时算在主页 / CV / 整理表上
  const urlSrc = (u, src) => {
    const k = /wikidata\.org/i.test(u) ? 'wikidata' : /wikipedia\.org/i.test(u) ? 'wikipedia' : /dblp\.(org|uni-trier\.de)/i.test(u) ? 'dblp'
      : /\.pdf($|[?#])|\bcv\b|vita/i.test(u) && src.includes('cv') ? 'cv' : null;
    if (k && src.includes(k)) return k;
    return ['homepage', 'cv', 'sheet'].find((s) => src.includes(s)) || null;
  };
  const edgeUrls = (e) => (Array.isArray(e.url) ? e.url : String(e.url || '').split(/\s+\|\s+|\n/)).map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  const shortUrl = (u) => { const t = u.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, ''); return t.length > 52 ? t.slice(0, 50) + '…' : t; };
  const relKind = (e) => (e.t === 'postdoc' ? 'postdoc advisor' : 'PhD advisor');

  function evBox(e) {
    const A = G.nodes.get(e.a), S = G.nodes.get(e.s);
    const by = {}, extra = [];
    for (const u of edgeUrls(e)) { const k = urlSrc(u, e.src); if (k) (by[k] = by[k] || []).push(u); else extra.push(u); }
    if (e.src.includes('dblp') && !by.dblp) by.dblp = ['https://dblp.org/search?q=' + encodeURIComponent(A.n)];
    const link = (u) => el('a', { class: 'ev-url', href: u, target: '_blank', rel: 'noopener' }, shortUrl(u), el('span', { 'aria-hidden': 'true' }, ' ↗'));
    return el('div', { class: 'ev-box', role: 'group', 'aria-label': `Evidence that ${A.n} was the ${relKind(e)} of ${S.n}` },
      el('p', { class: 'ev-head' }, el('b', {}, A.n), e.inferred ? ` is inferred to be the ${relKind(e)} of ` : ` is listed as the ${relKind(e)} of `, el('b', {}, S.n), '.'),
      e.disputed ? el('p', { class: 'ev-warn' }, el('b', {}, 'Unverified. '),
        'This comes from the curated sheet, but Wikidata or Wikipedia lists a different advisor for this person, so it is left out of paths and family lists.' + (e.note && !e.inferred ? ` ${e.note}` : '')) : null,
      el('ul', { class: 'ev-list' }, e.src.map((k) => el('li', { class: k === 'dblp' && e.inferred ? 'infer' : null },
        el('span', { class: 'ev-src' }, k === 'dblp' && !e.inferred ? 'dblp (agrees)' : SRC_NAME[k] || k),
        el('span', { class: 'ev-desc' }, k === 'dblp' && !e.inferred ? DBLP_AGREES : SRC_INFO[k] || '', k === 'dblp' && e.note ? el('span', { class: 'ev-note' }, ` Evidence: ${e.note}.`) : null),
        ...(by[k] || []).map(link)))),
      extra.length ? el('p', { class: 'ev-extra' }, 'Link: ', ...extra.map(link)) : null,
      el('button', { class: 'linkbtn inline ev-fix', type: 'button', onclick: () => openFix(S, e.src.length ? 'Wrong advisor' : 'Missing advisor') }, 'Is this link wrong?'));
  }
  // “来源”小按钮：点开在同一行下面展开证据框（手机上也好点，不用悬停）
  let evSeq = 0;
  function evChip(e) {
    const k = e.src.length;
    const A = G.nodes.get(e.a), S = G.nodes.get(e.s);
    const b = el('button', { class: `src ev ${e.inferred ? 'infer' : ''}`, type: 'button', 'aria-expanded': 'false',
      'aria-label': `Show the ${k > 1 ? k + ' sources' : 'source'} for ${A.n} as ${relKind(e)} of ${S.n}`, title: 'Where this link comes from' },
      k > 1 ? `${k} sources` : 'source');
    let box = null;
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (!box) {
        box = evBox(e); box.id = 'ev' + (++evSeq); box.hidden = true;
        b.setAttribute('aria-controls', box.id);
        const host = b.closest('[data-ev-host]') || b.closest('li');
        if (host) host.append(box); else b.parentElement.after(box);
      }
      box.hidden = !box.hidden;
      b.setAttribute('aria-expanded', String(!box.hidden));
      b.classList.toggle('open', !box.hidden);
    });
    return b;
  }
  const REPO = 'https://github.com/xinyi-liu-ai/academic-lineage-tree';
  // 纠错表单提交到 Formspree（https://formspree.io/f/<id>）。还没填时退回到预填好的 GitHub issue 页面
  const FIX_ENDPOINT = 'https://formspree.io/f/mqpqdpzz';
  const FIX_OUTSIDE = 'Listed as an outside researcher but is current faculty';  // 和 index.html 里的选项文字一致
  let fixFor = null, fixTimer = null;
  function openFix(n, type = '') {
    clearTimeout(fixTimer); fixTimer = null;  // 上一次“发送成功后自动关闭”的计时不能关掉这次新开的表单
    fixFor = n; fixSent = false;
    $('#fixWho').value = n ? `${n.n}${n.uni ? ' · ' + n.uni : n.af ? ' · ' + n.af : ''}` : 'General feedback';
    $('#fixType').value = type; $('#fixWhat').value = ''; $('#fixSrc').value = ''; $('#fixMsg').textContent = ''; $('#fixMsg').className = 'fix-msg';
    $('#fixSend').disabled = false; $('#fixSend').textContent = FIX_ENDPOINT ? 'Send' : 'Continue on GitHub';
    const d = $('#fixDialog');
    if (d.showModal) d.showModal(); else d.setAttribute('open', '');
    setTimeout(() => $(type ? '#fixWhat' : '#fixType').focus(), 0);
  }
  // 写了内容还没发送：关掉前先问一句（发送成功后自动关闭时不问）
  let fixSent = false;
  const fixDirty = () => !fixSent && !!($('#fixWhat').value.trim() || $('#fixSrc').value.trim());
  const confirmDiscard = () => !fixDirty() || confirm('Discard your unsent correction?');
  function closeFix(force = false) {
    if (force !== true && !confirmDiscard()) return;
    const d = $('#fixDialog'); if (d.close) d.close(); else d.removeAttribute('open');
  }
  // 来源链接没写 http(s):// 时自动补上 https://（例如 “cs.stanford.edu/~x”）
  const fixUrl = (s) => (s && !/^[a-z][a-z0-9+.-]*:/i.test(s) && /^[\w-]+(\.[\w-]+)+([/?#].*)?$/.test(s) ? 'https://' + s : s);
  async function sendFix(ev) {
    ev.preventDefault();
    const msg = $('#fixMsg');
    if (!$('#fixType').value || !$('#fixWhat').value.trim()) { msg.textContent = 'Please choose what’s wrong and describe the fix.'; msg.className = 'fix-msg err'; return; }
    const src = fixUrl($('#fixSrc').value.trim());
    $('#fixSrc').value = src;
    if (src && !/^https?:\/\//i.test(src)) { msg.textContent = 'The source should be a link, for example https://example.edu/cv.pdf.'; msg.className = 'fix-msg err'; return; }
    const data = { person: $('#fixWho').value, person_id: fixFor ? fixFor.id : '', type: $('#fixType').value, details: $('#fixWhat').value.trim(),
      source: src, email: $('#fixEmail').value.trim(), page: location.href, _gotcha: $('#fixForm').elements._gotcha.value };
    if (!FIX_ENDPOINT) {
      const body = `Person: ${data.person} (${data.person_id})\nPage: ${data.page}\n\nWhat's wrong: ${data.type}\n\nWhat it should be:\n${data.details}\n\nSource: ${data.source || '—'}\n`;
      open(`${REPO}/issues/new?` + new URLSearchParams({ title: `${data.type}: ${fixFor ? fixFor.n : 'site'}`, body }).toString(), '_blank', 'noopener');
      closeFix(true);
      return;
    }
    $('#fixSend').disabled = true; $('#fixSend').textContent = 'Sending…';
    try {
      const r = await fetch(FIX_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(data) });
      if (!r.ok) throw new Error(r.status);
      fixSent = true;
      msg.textContent = 'Thank you! We’ll check it and update the site.'; msg.className = 'fix-msg ok';
      $('#fixSend').textContent = 'Sent';
      fixTimer = setTimeout(() => closeFix(true), 1600);
    } catch (e) {
      msg.textContent = 'Could not send right now. Please try again in a moment.'; msg.className = 'fix-msg err';
      $('#fixSend').disabled = false; $('#fixSend').textContent = 'Send';
    }
  }
  // 没有主页时按“姓名 + 学校”搜网页（光按姓名搜领英常常搜到别人）
  const webSearch = (o) => {
    let ctx = o.ext ? '' : o.uni;
    if (!ctx) { const e = o.adv[0] || o.stu[0]; const p = e && G.nodes.get(o.adv[0] ? e.a : e.s); ctx = p && !p.ext ? p.uni : (o.af || ''); }
    return 'https://www.google.com/search?q=' + encodeURIComponent(`"${o.n}" ${ctx}`.trim());
  };

  function peopleList(edges, otherKey) {
    if (!edges.length) return el('p', { class: 'note' }, 'None recorded yet.');
    const sorted = edges.slice().sort((a, b) => a.disputed - b.disputed || a.inferred - b.inferred || (a.t === 'phd' ? -1 : 1));
    const LIMIT = 12;
    const ul = el('ul', { class: 'people' });
    const render = (all) => ul.replaceChildren(...sorted.slice(0, all ? Infinity : LIMIT).map((e) => {
      const o = G.nodes.get(e[otherKey]);
      const home = o.h || o.li;
      return el('li', {},
        el('button', { class: 'p-name', type: 'button', title: 'Show details', onclick: () => renderDrawer(o.id) }, o.n),
        el('small', {}, o.ext ? (o.af ? shortAff(o.af) : '') : o.uniShort), ...flagBadges(e), evChip(e),
        el('span', { class: 'p-acts' },
          home ? el('a', { class: 'plink', href: home, target: '_blank', rel: 'noopener', title: o.h ? 'Homepage' : 'LinkedIn' }, o.h ? 'Homepage' : 'LinkedIn') : null,
          el('button', { class: 'tree-btn labeled', type: 'button', title: `Open ${o.n}’s family tree`, onclick: () => go(o.id) },
            el('img', { src: 'assets/tree-icon.png', alt: '' }), 'Tree')));
    }), ...(sorted.length > LIMIT && !all ? [el('li', {}, el('button', { class: 'more-people', type: 'button', onclick: () => render(true) }, `Show all ${sorted.length}`))] : []));
    render(false);
    return ul;
  }

  // 读屏软件：详情换人时念一句“Details: 名字”（不把整个详情面板设成 live region）
  function announce(text) {
    const r = $('#srLive');
    r.textContent = '';
    setTimeout(() => { r.textContent = text; }, 30);
  }
  function renderDrawer(id, path, o = {}) {
    const n = G.nodes.get(id), d = $('#drawer');
    const hadFocus = d.contains(document.activeElement);  // 在详情里点了别人：焦点移到新标题上，不丢到页面开头
    const rel = relationTo(id);
    const links = [];
    const ext = (href, text, cls) => el('a', { href, target: '_blank', rel: 'noopener', class: cls || null }, text);
    if (n.h) { const a = ext(n.h, 'Homepage', 'primary'); if (n.hv) a.title = 'Linked from ' + n.hv; links.push(a); }
    if (n.li) links.push(ext(n.li, 'LinkedIn'));
    else if (!n.h) links.push(ext(webSearch(n), 'Search the web'));
    if (n.g) links.push(ext(n.g, 'Google Scholar'));
    if (n.c) links.push(ext(n.c, 'CV'));
    const fav = n.ext ? null : el('button', { class: `fav ${favs.has(id) ? 'on' : ''}`, type: 'button', 'aria-pressed': String(favs.has(id)) }, el('span', { class: 'star' }), favs.has(id) ? 'Starred' : 'Star');
    if (fav) fav.addEventListener('click', () => { toggleFav(id); renderDrawer(id, path, { refocus: '.fav', quiet: true }); });
    const h3 = el('h3', { tabindex: -1 }, n.n);
    d.replaceChildren(...[
      el('div', { class: 'who' }, avatar(n), el('div', {}, h3,
        n.ext ? null : el('div', { class: 'uni ti', title: n.as && isAP(n) ? `Homepage: “${n.as[2]}”` : null }, titleLine(n)), el('div', { class: 'uni' }, n.ext ? (n.af || (n.st ? 'PhD student (listed on an advisor’s page)' : 'Current position unknown')) : n.uni))),
      n.rc ? el('div', { class: `rc-box ${n.rc[0]}` },
        el('b', {}, n.rc[0] === 'yes' ? (rcUndated(n) ? (rcWeak(n) ? 'Mentions taking students (unclear)' : 'Open to students (no year given)') : rcLabel(n))
          : rcStale(n) ? `Not recruiting for ${n.rc[1]} · may have changed since` : n.rc[1] ? `Not recruiting for ${n.rc[1]}` : 'Not taking students'), ` — homepage, seen ${n.rc[2]}`,
        rcUndated(n) ? el('span', { class: 'rc-warn' }, rcFlags(n).inst ? `This seems to be about ${rcFlags(n).inst}, not ${n.uni}.`
          : rcFlags(n).phd === false ? 'It does not say PhD students specifically (it may mean undergraduates or research assistants).'
          : rcFlags(n).old ? 'The page does not seem to have been updated recently, so this may be old.'
          : 'No year is stated, so this may be old. Check the homepage or email before applying.') : null,
        el('q', {}, n.rc[3]), n.rc[4] ? el('a', { href: n.rc[4], target: '_blank', rel: 'noopener' }, 'Source') : null) : null,
      n.ext ? (n.usAcad && n.af
        // 在美国高校任教、只是不在 CSRankings 名单上：直说，并给一个“其实是在职教授”的纠错入口
        ? el('p', { class: 'note' }, `${/\b(professor|lecturer|faculty|instructor)\b/i.test(n.af) ? n.af : 'Faculty at ' + n.af} (not on CSRankings’ list). `,
          el('button', { class: 'linkbtn inline', type: 'button', onclick: () => openFix(n, FIX_OUTSIDE) }, 'Current faculty here? Tell us'))
        : el('p', { class: 'note' }, `Not on the CSRankings faculty list of these ${G.unis.length} US universities (for example in industry, abroad or another field).`)) : null,
      n.nf ? el('p', { class: 'note nf-note' }, el('span', { class: 'nf-badge' }, 'New faculty'), ' Joined recently and not yet on the CSRankings faculty list. Added from ',
        el('a', { href: n.nf, target: '_blank', rel: 'noopener' }, 'the university’s announcement'), '.') : null,
      n.former ? el('p', { class: 'note' }, 'No longer on CSRankings’ current faculty list for these institutions (moved, retired or emeritus)' + (n.now ? `; now listed at ${n.now}.` : '.')) : null,
      rel ? el('p', { class: 'note' }, rel[1], ' ', ...flagBadges(rel[0]), evChip(rel[0])) : null,
      path && path.length > 2 ? el('h4', {}, `How ${n.n} is related to ${G.nodes.get(path[0]).n}`) : null,
      path && path.length > 2 ? pathChain(path, { noinf: F.noinf, tags: ['This tree', 'Selected'], sum: (sig) => [`Relationship to ${G.nodes.get(path[0]).n}: `, el('b', {}, relLabel(sig))] }) : null,
      n.ext || !n.d.length ? null : el('div', { class: 'tags' }, n.d.map((x) => el('span', { class: 'tag' }, SUB_LABEL[x] || x))),
      links.length ? el('div', { class: 'links-row' }, links) : null,
      el('div', { class: 'actions' }, fav, id !== T.root ? el('button', { class: 'btn-dark', type: 'button', onclick: () => go(id) }, `Explore ${n.n.split(' ')[0]}’s family tree`) : null,
        el('a', { class: 'cx-link', href: '#/connect?' + new URLSearchParams({ a: id }), title: `See how ${n.n} is connected to someone else` }, 'Connect with…')),
      el('button', { class: 'fix-btn', type: 'button', onclick: () => openFix(n) }, 'Suggest a correction'),
      el('h4', {}, `Advisors (${n.adv.length})`), peopleList(n.adv, 'a'),
      el('h4', {}, `Students (${n.stu.length})`), peopleList(n.stu, 's'),
      el('p', { class: 'note' }, 'Student lists are incomplete: they only include students we could find in public records. ',
        el('button', { class: 'linkbtn inline', type: 'button', onclick: () => openFix(n, 'Wrong or missing student') }, 'Missing someone or something wrong?'))].filter(Boolean));
    const tab = $('#tabInfo');
    tab.disabled = false;
    tab.textContent = `Details · ${n.n}`;
    sidePane('info');
    if (o.refocus) { const t = $(o.refocus, d); if (t) t.focus({ preventScroll: true }); return; }
    d.scrollTop = 0;
    announce(`Details: ${n.n}`);
    if (hadFocus) h3.focus({ preventScroll: true });
    // 手机上侧栏在树下面：滚过去让人看见
    if (innerWidth <= 1000 && !o.quiet) $('#family').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  // 侧栏两个标签：家族名单 / 人物详情。切回名单时保留树上的路径高亮
  function sidePane(which) {
    const info = which === 'info';
    $('#paneFam').hidden = info;
    $('#drawer').hidden = !info;
    for (const [t, on] of [[$('#tabFam'), !info], [$('#tabInfo'), info]]) {
      t.classList.toggle('on', on); t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1;
    }
  }
  // 标签页键盘操作：左右方向键切换（自动激活），Home / End 到两头
  function tabKey(e) {
    const tabs = [$('#tabFam'), $('#tabInfo')].filter((t) => !t.disabled);
    const i = tabs.indexOf(e.target);
    if (i < 0) return;
    let j;
    if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') j = 0;
    else if (e.key === 'End') j = tabs.length - 1;
    else return;
    e.preventDefault();
    tabs[j].focus();
    sidePane(tabs[j] === $('#tabInfo') ? 'info' : 'fam');
  }
  function closeDrawer() {
    const hadFocus = $('#drawer').contains(document.activeElement);
    sidePane('fam');
    if (hadFocus) $('#tabFam').focus();
    if (T.path) { T.path = null; drawTree(); for (const li of document.querySelectorAll('#famList .fam-row.sel')) li.classList.remove('sel'); }
  }

  const tip = $('#tip');
  function showTip(ev, n, e) {
    tip.textContent = [n.n, n.ext ? (n.af || null) : (n.ti ? n.ti + ', ' : '') + n.uni + (n.former ? ' (former)' : ''),
      e ? `${e.t === 'phd' ? 'PhD' : 'Postdoc'}${e.disputed ? ' · unverified' : ''}${e.inferred ? ' · inferred from co-authorship' : ''}` : null,
      'Click for details · double-click to re-center'].filter(Boolean).join('\n');
    tip.hidden = false;
    tip.style.left = Math.max(8, Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8)) + 'px';
    // 靠近窗口底部时放到指针上方，不超出屏幕
    const h = tip.offsetHeight;
    tip.style.top = Math.max(8, ev.clientY + 14 + h > innerHeight - 8 ? ev.clientY - h - 10 : ev.clientY + 14) + 'px';
  }
  function hideTip() { tip.hidden = true; }

  function openTree(id) {
    if (T.root !== id) Object.assign(T, { root: id, focus: { up: [], down: [] }, page: {}, lo: null, selected: id, path: null });
    const n = G.nodes.get(id);
    $('#treeTitle').replaceChildren(n.n, el('small', {}, whereLabel(n)));
    document.title = `${n.n} · Academic Lineage Family Tree`;
    closeDrawer();
    $('#tabInfo').disabled = true; $('#tabInfo').textContent = 'Details';
    drawTree();
    renderFamily();
    syncHash(famHash());
  }

  /* ---------- academic family list ---------- */
  // 从中心人物出发，沿师生关系（上 = 导师方向，下 = 学生方向）做广度优先搜索，记下最短路径的上下序列
  // nwu = 新 AP 里也算上没找到入职年份的 AP；rco = 招生里也算上没写年份、但说招 PhD 的
  const F_DEFAULT = { dist: 2, ap: false, nw: false, nwu: false, rc: false, rco: false, area: false, all: false, noinf: false };
  const F = { ...F_DEFAULT, cache: null };
  const F_KEYS = [['ap', 'ap'], ['nw', 'new'], ['nwu', 'newunknown'], ['rc', 'recruiting'], ['rco', 'open'], ['area', 'area'], ['all', 'all'], ['noinf', 'noinferred']];
  const FAM_TOGGLES = [['famAP', 'ap'], ['famNew', 'nw'], ['famNewUnk', 'nwu'], ['famRC', 'rc'], ['famRCOpen', 'rco'], ['famArea', 'area'], ['famAll', 'all'], ['famNoInf', 'noinf']];
  // 子选项（“+ …”）依附于主选项：勾子选项会顺带勾上主选项，取消主选项时子选项一起取消
  const F_SUB = { nwu: 'nw', rco: 'rc' };
  const famFiltersOn = () => FAM_TOGGLES.filter(([, k]) => F[k] && !F_SUB[k]).length + (F.dist !== F_DEFAULT.dist ? 1 : 0);
  const resetFam = () => Object.assign(F, F_DEFAULT);
  // 分享链接带上家族名单的筛选：#/p/<id>?within=3&new=1&recruiting=1
  const famHash = () => {
    const q = [];
    if (F.dist !== 2) q.push('within=' + F.dist);
    for (const [k, u] of F_KEYS) if (F[k]) q.push(u + '=1');
    return '#/p/' + encodeURIComponent(T.root) + (q.length ? '?' + q.join('&') : '');
  };
  const syncHash = (h) => { if (location.hash !== h) history.replaceState(null, '', h); };
  const REL = {
    uu: 'Grand-advisor', uuu: 'Great-grand-advisor', uuuu: 'Great-great-grand-advisor',
    dd: 'Grand-student', ddd: 'Great-grand-student', dddd: 'Great-great-grand-student',
    ud: 'Lab sibling (same advisor)', udd: 'Student of a lab sibling', uddd: 'Grand-student of a lab sibling',
    uud: 'Advisor’s lab sibling', uudd: 'Academic cousin', uuud: 'Grand-advisor’s lab sibling',
    du: 'Co-advisor of a student', dud: 'Student of a co-advisor', duu: 'Advisor of a co-advisor',
  };
  // 一步之遥的写明是谁的：“Student” 容易被看成“这人是个学生”，改成 “Student of Jeffrey Heer”
  // 一步关系要看边的类型：博士后写 Postdoc，不写 Student
  const relLabel = (sig, id) => {
    const root = G.nodes.get(T.root);
    if ((sig === 'u' || sig === 'd') && id) {
      const e = sig === 'u' ? root.adv.find((x) => x.a === id) : root.stu.find((x) => x.s === id);
      if (e && e.t === 'postdoc') return sig === 'u' ? `Postdoc advisor of ${root.n}` : `Postdoc of ${root.n}`;
    }
    return sig === 'u' ? `Advisor of ${root.n}` : sig === 'd' ? `Student of ${root.n}` : REL[sig] || `${sig.length} steps away`;
  };

  function familyOf(rootId, maxDist) {
    const seen = new Map([[rootId, '']]), prev = new Map(), inf = new Map();
    let frontier = [rootId];
    for (let k = 1; k <= maxDist && frontier.length; k++) {
      const next = [];
      for (const id of frontier) {
        const n = G.nodes.get(id), sig = seen.get(id);
        const ok = (e) => !e.disputed && !(F.noinf && e.inferred);
        for (const e of n.adv) if (ok(e) && !seen.has(e.a)) { seen.set(e.a, sig + 'u'); prev.set(e.a, id); inf.set(e.a, inf.get(id) || e.inferred); next.push(e.a); }
        for (const e of n.stu) if (ok(e) && !seen.has(e.s)) { seen.set(e.s, sig + 'd'); prev.set(e.s, id); inf.set(e.s, inf.get(id) || e.inferred); next.push(e.s); }
      }
      frontier = next;
    }
    seen.delete(rootId);
    return { map: seen, prev, inf };
  }
  const pathTo = (id) => { const p = [id]; while (F.cache.prev.has(p[0])) p.unshift(F.cache.prev.get(p[0])); return p; };

  // 点名单里的人：树上把这条连接路径标出来（从中心人物出发、方向不变的那一段能画在树上），详情里列出整条路径
  function showPath(id) {
    const path = pathTo(id), sig = F.cache.map.get(id);
    let k = 0;
    while (k < sig.length && sig[k] === sig[0]) k++;
    const dir = sig[0] === 'u' ? 'up' : 'down', sign = dir === 'down' ? 1 : -1;
    T.focus[dir] = path.slice(1, k + 1);
    for (const key of Object.keys(T.page)) if (Math.sign(+key) === sign) delete T.page[key];
    T.path = new Set(path);
    T.pathEnd = sign * k;
    T.selected = id;
    drawTree();
    renderDrawer(id, path);
    for (const li of document.querySelectorAll('#famList .fam-row')) li.classList.toggle('sel', li.dataset.id === id);
  }

  // 两人之间每一步用哪条边：y 是 x 的导师或学生；有多条时优先有来源的、博士的
  const edgeOk = (noinf) => (e) => !e.disputed && !(noinf && e.inferred);
  function stepEdge(x, y, noinf) {
    const nx = G.nodes.get(x);
    const es = nx.adv.filter((e) => e.a === y).concat(nx.stu.filter((e) => e.s === y));
    es.sort((p, q) => p.disputed - q.disputed || p.inferred - q.inferred || (p.t === 'phd' ? 0 : 1) - (q.t === 'phd' ? 0 : 1));
    return es.find(edgeOk(noinf)) || es[0];
  }
  // 路径的上下序列：u = 下一个人是导师，d = 下一个人是学生
  const pathSig = (path, noinf) => path.slice(1).map((y, i) => (stepEdge(path[i], y, noinf).a === y ? 'u' : 'd')).join('');

  // 连接路径画成一棵小家谱：导师在上、学生缩进在下，每一行都写成“X 的博士学生”，不用箭头。
  // 任意路径都按“山峰”切段：每段先列共同的导师（峰顶），再列两侧往下的学生；
  // 两段之间的“谷底”是两位导师共同的学生，只列一次
  function pathChain(path, o = {}) {
    const noinf = !!o.noinf;
    const edges = path.slice(1).map((y, i) => stepEdge(path[i], y, noinf));
    const sig = edges.map((e, i) => (e.a === path[i + 1] ? 'u' : 'd')).join('');
    const L = path.length - 1, first = path[0], last = path[L];
    const [tagA, tagB] = o.tags || ['This tree', 'Selected'];
    const nm = (id) => G.nodes.get(id).n;
    const kind = (e) => (e.t === 'postdoc' ? 'Postdoc' : 'PhD');
    const rel = (text, e) => el('span', { class: 'pc-rel' }, text, ...(e ? [...flagBadges(e).filter(Boolean).map((b) => [' ', b]).flat(), ' ', evChip(e)] : []));
    const row = (i, depth, rels) => {
      const id = path[i], n = G.nodes.get(id);
      const tag = i === 0 ? el('span', { class: 'pc-tag' }, tagA) : i === L ? el('span', { class: 'pc-tag sel' }, tagB) : null;
      return el('li', { class: `pc-row ${depth ? '' : 'top'} ${i === 0 ? 'start' : ''} ${i === L ? 'end' : ''}`, style: `--depth:${depth}` },
        avatar(n),
        el('span', { class: 'pc-text', 'data-ev-host': '' },
          el('span', { class: 'pc-name' }, el('a', { href: '#/p/' + encodeURIComponent(id), class: 'pc-link', title: `Open ${n.n}’s family tree` }, n.n), tag),
          el('small', {}, n.ext ? outsideSub(n) : `${titleLine(n, true)} · ${o.full ? n.uni : n.uniShort}`),
          ...rels.filter(Boolean)));
    };
    const ol = el('ol', { class: 'path-chain' });
    // 切段：谷底 = 前一步往下、后一步往上
    const cuts = [0];
    for (let i = 1; i < L; i++) if (sig[i - 1] === 'd' && sig[i] === 'u') cuts.push(i);
    cuts.push(L);
    for (let k = 0; k + 1 < cuts.length; k++) {
      const s = cuts[k], e = cuts[k + 1];
      let t = s;
      while (t < e && sig[t] === 'u') t++;
      const both = t > s && t < e;
      // 峰顶；如果它直接连着上一段的谷底（这一段左侧没有别人），在这里写明它也是谷底那人的导师
      const nearValley = k > 0 && t === s + 1;
      const peakRels = [both && !nearValley ? rel(`Shared advisor of ${nm(path[t - 1])} and ${nm(path[t + 1])}`) : null];
      if (nearValley) peakRels.push(rel(`${kind(edges[s])} advisor of ${nm(path[s])} (above)`, edges[s]));
      ol.append(row(t, 0, peakRels));
      for (let j = t - 1; j >= s; j--) {
        if (k > 0 && j === s) break;  // 谷底那人已经在上一段列过
        const rels = [rel(`${kind(edges[j])} student of ${nm(path[j + 1])}`, edges[j])];
        if (k > 0 && j === s + 1) rels.push(rel(`${kind(edges[s])} advisor of ${nm(path[s])} (above)`, edges[s]));
        ol.append(row(j, t - j, rels));
      }
      for (let j = t + 1; j <= e; j++) ol.append(row(j, j - t, [rel(`${kind(edges[j - 1])} student of ${nm(path[j - 1])}`, edges[j - 1])]));
    }
    const sum = o.sum ? o.sum(sig) : null;
    return el('div', { class: 'path-box' }, sum ? el('p', { class: 'pc-sum' }, sum) : null, ol);
  }

  function renderFamily() {
    const root = G.nodes.get(T.root);
    if (!root) return;
    if (!F.cache || F.cache.root !== T.root || F.cache.noinf !== F.noinf) F.cache = { root: T.root, noinf: F.noinf, ...familyOf(T.root, 4) };
    for (const [id, key] of FAM_TOGGLES) $('#' + id).checked = F[key];
    $('#famNewWrap').hidden = $('#famNewUnkWrap').hidden = !G.hasAS;
    for (const b of document.querySelectorAll('.fam-controls [data-dist]')) { b.classList.toggle('on', +b.dataset.dist === F.dist); b.setAttribute('aria-pressed', String(+b.dataset.dist === F.dist)); }
    const areas = new Set(root.ext ? [] : root.a);
    $('#famArea').disabled = !areas.size;
    // skip：算某一组开关的人数时，先不套用那一组（“新 AP” / “招生”），其余筛选照常
    const pass = (n, sig, skip) => sig.length <= F.dist && (F.all || !n.ext || n.usAcad) && (!F.ap || isAP(n))
      && (skip === 'nw' || !F.nw || isNewAP(n) || (F.nwu && apYearUnknown(n)))
      && (skip === 'rc' || !F.rc || rcMatch(n, F.rco))
      && (!F.area || !areas.size || (!n.ext && n.a.some((a) => areas.has(a))));
    const rows = [], forNw = [], forRc = [];
    for (const [id, sig] of F.cache.map) {
      const n = G.nodes.get(id);
      if (pass(n, sig)) rows.push({ n, sig });
      if (pass(n, sig, 'nw')) forNw.push(n);
      if (pass(n, sig, 'rc')) forRc.push(n);
    }
    const nNew = forNw.filter(isNewAP).length, nUnk = forNw.filter(apYearUnknown).length;
    const rcc = rcCounts(forRc);
    $('#famNewLabel').textContent = `New APs (since ${ADMIT_YEAR - 3}) · ${fmt(nNew)}`;
    $('#famNewUnkLabel').textContent = `+ ${fmt(nUnk)} AP${nUnk === 1 ? '' : 's'}, start year unknown`;
    $('#famRCLabel').textContent = `Recruiting for ${ADMIT_YEAR} · ${fmt(rcc.rc)}`;
    $('#famRCOpenLabel').textContent = `+ ${fmt(rcc.open)} open to students (no year)`;
    const on = famFiltersOn();
    $('#famFilters').hidden = !on;
    $('#famFiltersText').textContent = `${on} filter${on > 1 ? 's' : ''} on`;
    // 近的在前；同样远时招生的、AP 在前
    rows.sort((x, y) => x.sig.length - y.sig.length || recruiting(y.n) - recruiting(x.n) || isAP(y.n) - isAP(x.n)
      || ((x.n.ext && !x.n.usAcad) - (y.n.ext && !y.n.usAcad)) || (x.n.ext - y.n.ext) || y.n.degree - x.n.degree);
    const fac = rows.filter((r) => !r.n.ext || r.n.usAcad).length;
    $('#famTitle').textContent = `${root.n}’s academic family`;
    $('#famSub').textContent = `${fmt(fac)} US faculty within ${F.dist} step${F.dist > 1 ? 's' : ''}` +
      (F.all ? ` · ${fmt(rows.length - fac)} others` : '') +
      ` · ${fmt(rows.filter((r) => isAP(r.n)).length)} assistant professor${rows.filter((r) => isAP(r.n)).length === 1 ? '' : 's'}` +
      ` · ${rcSummary(rcCounts(rows.map((r) => r.n)))}`;
    const LIMIT = 200;
    $('#famList').replaceChildren(...(rows.length ? rows.slice(0, LIMIT).map(({ n, sig }) => {
      const li = el('li', { class: `fam-row d${Math.min(sig.length, 4)} ${n.ext && !n.usAcad ? 'out' : ''} ${T.selected === n.id ? 'sel' : ''}`, 'data-id': n.id },
        el('button', { class: 'fam-main', type: 'button', onclick: () => showPath(n.id), title: 'Show how they are connected' },
          avatar(n, true),
          el('span', { class: 'fam-text' },
            el('span', { class: 'fam-name' }, n.n),
            el('span', { class: 'fam-rel' }, relLabel(sig, n.id), F.cache.inf.get(n.id) ? el('span', { class: 'src infer', title: 'The connection includes an advisor link inferred from dblp co-authorship' }, 'inferred') : null),
            el('span', { class: 'fam-meta', title: n.ext ? n.af || '' : '' }, n.ext ? outsideSub(n) + (n.usAcad ? ' · not on CSRankings' : '') : `${titleLine(n, true)} · ${n.uniShort}`),
            rcBadge(n))),
        el('button', { class: 'tree-btn labeled', type: 'button', title: `Open ${n.n}’s family tree`, onclick: () => go(n.id) },
          el('img', { src: 'assets/tree-icon.png', alt: '' }), 'Tree'));
      return li;
    }) : [el('li', { class: 'fam-empty' }, F.rc && !F.rco && rcc.open ? `Nobody in this range gives ${ADMIT_YEAR} on their homepage. Turn on “+ open to students (no year)” to see ${fmt(rcc.open)} more.`
      : F.nw && !F.nwu && nUnk ? `No assistant professor here started in ${ADMIT_YEAR - 3} or later as far as we know. ${fmt(nUnk)} more ${nUnk === 1 ? 'has' : 'have'} no start year on record: turn on “+ start year unknown”.`
      : F.rc ? 'Nobody in this range says on their homepage that they are recruiting. Try a larger range or turn off the filter.' : 'Nobody matches. Try a larger range or fewer filters.')]),
    ...(rows.length > LIMIT ? [el('li', { class: 'fam-empty' }, `Showing the closest ${LIMIT} of ${fmt(rows.length)}.`)] : []));
  }

  /* ---------- how are two people connected? ---------- */
  const CX_MAX = 8;
  const C = { a: null, b: null, noinf: false };
  const connectHash = () => {
    const q = new URLSearchParams();
    if (C.a) q.set('a', C.a);
    if (C.b) q.set('b', C.b);
    if (C.noinf) q.set('noinferred', '1');
    const s = q.toString();
    return '#/connect' + (s ? '?' + s : '');
  };
  // 两个排序键按顺序比大小
  const keyLess = (x, y) => { for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i]; return false; };

  // 双向广度优先：每次扩展较小的一边，两边相遇时就是最短距离；记下同一层的所有前驱，用来列出所有最短路径
  function connectPaths(a, b, noinf, maxD = CX_MAX) {
    if (a === b) return null;
    const ok = edgeOk(noinf);
    const nbrs = (id) => {
      const n = G.nodes.get(id), out = new Set();
      for (const e of n.adv) if (ok(e)) out.add(e.a);
      for (const e of n.stu) if (ok(e)) out.add(e.s);
      return out;
    };
    const A = { dist: new Map([[a, 0]]), par: new Map(), front: [a], k: 0 };
    const B = { dist: new Map([[b, 0]]), par: new Map(), front: [b], k: 0 };
    let meet = [];
    while (A.front.length && B.front.length && A.k + B.k < maxD) {
      const X = A.front.length <= B.front.length ? A : B, Y = X === A ? B : A;
      const k = ++X.k, next = [];
      for (const id of X.front) for (const o of nbrs(id)) {
        if (!X.dist.has(o)) { X.dist.set(o, k); X.par.set(o, [id]); next.push(o); }
        else if (X.dist.get(o) === k && !X.par.get(o).includes(id)) X.par.get(o).push(id);
      }
      X.front = next;
      meet = next.filter((o) => Y.dist.has(o));
      if (meet.length) break;
    }
    if (!meet.length) return null;
    const d = A.k + B.k;
    // 最短路径条数（可能很多，只用来显示“共 N 条”）
    const counter = (S, start) => {
      const memo = new Map([[start, 1]]);
      const c = (x) => { if (!memo.has(x)) memo.set(x, S.par.get(x).reduce((t, p) => t + c(p), 0)); return memo.get(x); };
      return c;
    };
    const cA = counter(A, a), cB = counter(B, b);
    const total = meet.reduce((t, m) => t + cA(m) * cB(m), 0);
    const chains = (S, start, node, limit) => {
      const res = [];
      const rec = (x, acc) => { if (res.length >= limit) return; if (x === start) { res.push([start, ...acc]); return; } for (const p of S.par.get(x)) rec(p, [x, ...acc]); };
      rec(node, []);
      return res;
    };
    const all = [];
    outer: for (const m of meet) {
      const right = chains(B, b, m, 40).map((cb) => cb.slice().reverse().slice(1));
      for (const ca of chains(A, a, m, 40)) for (const rb of right) {
        all.push(ca.concat(rb));
        if (all.length >= 300) break outer;
      }
    }
    // 排序：推断的边少、形状是“共同导师”式的、中间人多是在职教授的排前面；再挑彼此不同的 3 条
    const info = all.map((p) => {
      const edges = p.slice(1).map((y, i) => stepEdge(p[i], y, noinf));
      const sig = edges.map((e, i) => (e.a === p[i + 1] ? 'u' : 'd')).join('');
      return { p, sig, inf: edges.filter((e) => e.inferred).length, shape: /^u*d*$/.test(sig) ? 0 : /^d*u*$/.test(sig) ? 1 : 2,
        ext: p.slice(1, -1).filter((id) => G.nodes.get(id).ext).length };
    });
    info.sort((x, y) => x.inf - y.inf || x.shape - y.shape || x.ext - y.ext);
    const picked = [], used = new Set();
    while (picked.length < 3 && picked.length < info.length) {
      let best = null, bestKey = null;
      for (const c of info) {
        if (picked.includes(c)) continue;
        const novel = c.p.slice(1, -1).filter((id) => !used.has(id)).length;
        const key = [c.inf, -novel, c.shape, c.ext];
        if (!best || keyLess(key, bestKey)) { best = c; bestKey = key; }
      }
      picked.push(best);
      for (const id of best.p) used.add(id);
    }
    // 共同的导师（峰顶）/ 共同的学生（谷底），从所有列出的最短路径里收集
    const peaks = new Set(), valleys = new Set();
    for (const c of info) {
      if (c.shape === 0) { const t = c.sig.match(/^u*/)[0].length; if (t > 0 && t < c.sig.length) peaks.add(c.p[t]); }
      else if (c.shape === 1) { const t = c.sig.match(/^d*/)[0].length; if (t > 0 && t < c.sig.length) valleys.add(c.p[t]); }
    }
    return { d, total, paths: picked, peaks: [...peaks], valleys: [...valleys] };
  }

  // 关系的叫法：u = 往导师方向一步，d = 往学生方向一步（从 A 出发）
  const genWord = (k, base) => (k === 1 ? base : k === 2 ? 'grand-' + base : 'great-'.repeat(k - 2) + 'grand-' + base);
  const capFirst = (s) => s[0].toUpperCase() + s.slice(1);
  const ORD = ['', '', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh'];
  const TIMES = ['', 'once', 'twice', 'three times', 'four times', 'five times', 'six times'];
  function relInfo(sig, A, B) {
    const a = A.n, b = B.n;
    let m = sig.match(/^(u*)(d*)$/);
    if (m) {
      const i = m[1].length, j = m[2].length;
      if (!j) return { label: `${b} is ${a}’s ${genWord(i, 'advisor')}`, short: capFirst(genWord(i, 'advisor')), text: i === 1 ? `${b} advised ${a}.` : `A direct line: ${b} is ${i} academic generations above ${a}.` };
      if (!i) return { label: `${a} is ${b}’s ${genWord(j, 'advisor')}`, short: capFirst(genWord(j, 'student')), text: j === 1 ? `${a} advised ${b}.` : `A direct line: ${a} is ${j} academic generations above ${b}.` };
      const same = `${a}’s ${genWord(i, 'advisor')} is ${b}’s ${genWord(j, 'advisor')}.`;
      if (i === 1 && j === 1) return { label: 'Lab siblings', short: 'Lab siblings', text: `${a} and ${b} were trained by the same advisor.` };
      if (i === 1) return { label: `${b} is a ${genWord(j - 1, 'student')} of ${a}’s lab sibling`, short: `Lab sibling’s ${genWord(j - 1, 'student')}`, text: same };
      if (j === 1) return { label: `${b} is ${a}’s ${genWord(i - 1, 'advisor')}’s lab sibling`, short: `${capFirst(genWord(i - 1, 'advisor'))}’s lab sibling`, text: same };
      const c = Math.min(i, j) - 1, r = Math.abs(i - j);
      const word = `${c > 1 ? (ORD[c] || c + 'th') + ' ' : ''}academic cousins${r ? `, ${TIMES[r] || r + ' times'} removed` : ''}`;
      return { label: capFirst(word), short: capFirst(word), text: `They share an academic ancestor: ${same}` };
    }
    m = sig.match(/^(d+)(u+)$/);
    if (m) {
      const i = m[1].length, j = m[2].length;
      if (i === 1 && j === 1) return { label: 'Co-advisors', short: 'Co-advisors', text: `${a} and ${b} both advised the same student.` };
      return { label: 'They share an academic descendant', short: 'Shared descendant', text: `${a}’s ${genWord(i, 'student')} is ${b}’s ${genWord(j, 'student')}.` };
    }
    return { label: `Connected in ${sig.length} steps`, short: `${sig.length} steps`, text: `No single shared advisor or student links them; the shortest chain of advisor–student links passes through ${sig.length - 1} people.` };
  }

  let cxA = null, cxB = null;
  function mountConnect() {
    const pickInto = (key) => (n) => {
      C[key] = n ? n.id : null;
      syncHash(connectHash());
      renderConnect();
      if (n && key === 'a' && !C.b) cxB.input.focus();
    };
    cxA = nameSearch({ keep: true, id: 'cxA', placeholder: 'First person, e.g. Danqi Chen', label: 'Person A', onPick: pickInto('a') });
    cxB = nameSearch({ keep: true, id: 'cxB', placeholder: 'Second person, e.g. Christopher Manning', label: 'Person B', onPick: pickInto('b') });
    const swap = el('button', { class: 'cx-swap', type: 'button', 'aria-label': 'Swap the two people', title: 'Swap A and B' }, el('span', { 'aria-hidden': 'true' }, '⇄'));
    swap.addEventListener('click', () => { [C.a, C.b] = [C.b, C.a]; syncHash(connectHash()); renderConnect(); });
    $('#cxForm').replaceChildren(
      el('div', { class: 'cx-field' }, el('label', { class: 'lab', for: 'cxA' }, 'Person A'), cxA),
      swap,
      el('div', { class: 'cx-field' }, el('label', { class: 'lab', for: 'cxB' }, 'Person B'), cxB));
    $('#cxNoInf').addEventListener('change', (e) => { C.noinf = e.target.checked; syncHash(connectHash()); renderConnect(); });
    $('#cxCopy').addEventListener('click', async () => {
      const b = $('#cxCopy');
      try { await navigator.clipboard.writeText(location.href); b.textContent = 'Link copied'; } catch (e) { b.textContent = 'Copy failed'; }
      setTimeout(() => { b.textContent = 'Copy link'; }, 1600);
    });
  }

  const personChip = (n) => el('a', { class: 'cx-person', href: '#/p/' + encodeURIComponent(n.id), title: `Open ${n.n}’s family tree` },
    avatar(n, true), el('span', {}, el('b', {}, n.n), el('small', {}, n.ext ? outsideSub(n) : n.uni)));
  const setNoInf = (v) => () => { C.noinf = v; syncHash(connectHash()); renderConnect(); };

  let cxExamples = null;
  function renderConnect() {
    const A = C.a && G.nodes.get(C.a), B = C.b && G.nodes.get(C.b);
    cxA.set(A); cxB.set(B);
    $('#cxNoInf').checked = C.noinf;
    $('#cxCopy').hidden = !(A && B);
    document.title = (A && B ? `${A.n} & ${B.n}` : 'How are two people connected?') + ' · Academic Lineage Family Tree';
    const out = $('#cxOut');
    const msg = (...kids) => el('div', { class: 'cx-msg' }, ...kids);
    if (!A || !B) {
      // 示例：几位知名教授里能连上、又不是直接师生的两两组合
      if (!cxExamples) {
        const ex = ['Danqi Chen', 'Christopher D. Manning', 'Jeffrey Heer', 'Li Fei-Fei', 'Barbara Liskov', 'Dan Klein'].map((nm) => G.profs.find((p) => p.n === nm && !p.former)).filter(Boolean);
        cxExamples = [];
        for (let i = 0; i < ex.length && cxExamples.length < 3; i++) for (let j = i + 1; j < ex.length && cxExamples.length < 3; j++) {
          const r = connectPaths(ex[i].id, ex[j].id, false, 6);
          if (r && r.d >= 2) cxExamples.push([ex[i], ex[j]]);
        }
      }
      out.replaceChildren(msg(el('p', {}, A || B ? `Now choose ${A ? 'a second' : 'a first'} person.` : 'Choose two people to see the shortest chain of advisor–student links between them.'),
        !A && !B && cxExamples.length ? el('p', { class: 'cx-try' }, 'Try', ...cxExamples.map(([x, y]) =>
          el('a', { href: '#/connect?' + new URLSearchParams({ a: x.id, b: y.id }) }, `${x.n} & ${y.n}`))) : null));
      return;
    }
    if (A.id === B.id) { out.replaceChildren(msg(el('p', {}, 'Those are the same person. Choose someone else for Person B.'))); return; }
    const r = connectPaths(A.id, B.id, C.noinf);
    if (!r) {
      const alt = C.noinf ? connectPaths(A.id, B.id, false) : null;
      const lonely = [A, B].filter((n) => !n.adv.some(edgeOk(C.noinf)) && !n.stu.some(edgeOk(C.noinf)));
      out.replaceChildren(msg(
        el('h3', { class: 'cx-rel' }, `No connection found within ${CX_MAX} steps`),
        el('p', {}, `We could not link ${A.n} and ${B.n} through ${CX_MAX} or fewer advisor–student links` + (C.noinf ? ' without inferred links.' : '.')),
        lonely.length ? el('p', {}, `${lonely.map((n) => n.n).join(' and ')} ${lonely.length > 1 ? 'have' : 'has'} no recorded advisor or students${C.noinf ? ' from a source' : ''} yet. `,
          el('button', { class: 'linkbtn inline', type: 'button', onclick: () => openFix(lonely[0], 'Missing advisor') }, 'Know who it is?')) : null,
        alt ? el('p', {}, `Counting links inferred from dblp, they are ${alt.d} steps apart. `,
          el('button', { class: 'linkbtn inline', type: 'button', onclick: setNoInf(false) }, 'Include inferred links')) : null,
        el('p', { class: 'muted' }, 'Our records are incomplete, so a connection may still exist.')));
      return;
    }
    const info = relInfo(r.paths[0].sig, A, B);
    const anyInf = r.paths.some((c) => c.inf);
    const people = (ids) => [...ids.slice(0, 6).map((id) => personChip(G.nodes.get(id))), ids.length > 6 ? el('span', { class: 'muted' }, `+${ids.length - 6} more`) : null];
    const common = (lab, ids) => (ids.length ? el('div', { class: 'cx-common' }, el('span', { class: 'cx-common-lab' }, lab + (ids.length > 1 ? 's' : '')), el('div', { class: 'cx-people' }, people(ids))) : null);
    out.replaceChildren(
      el('div', { class: 'cx-summary' },
        el('p', { class: 'cx-kicker' }, `${r.d} step${r.d > 1 ? 's' : ''} apart · ` +
          (r.total > r.paths.length ? `showing ${r.paths.length} of ${fmt(r.total)} shortest paths` : r.total > 1 ? `${r.total} shortest paths` : 'one shortest path')),
        el('h3', { class: 'cx-rel' }, info.label),
        el('p', { class: 'cx-sent' }, info.text),
        common(r.d === 2 ? 'Shared advisor' : 'Shared academic ancestor', r.peaks),
        common(r.d === 2 ? 'Shared student' : 'Shared academic descendant', r.valleys),
        anyInf ? el('p', { class: 'cx-inf' }, el('span', { class: 'src infer', title: INFER_TIP }, 'inferred'),
          ` ${r.paths.every((c) => c.inf) ? 'Every path shown includes' : 'Some paths include'} a link inferred from dblp co-authorship rather than recorded in a source. `,
          el('button', { class: 'linkbtn inline', type: 'button', onclick: setNoInf(true) }, 'Exclude inferred links')) : null),
      el('div', { class: 'cx-paths' }, r.paths.map((c, i) => el('section', { class: 'cx-path', 'aria-label': `Path ${i + 1}` },
        el('h4', {}, r.paths.length > 1 ? `Path ${i + 1}` : 'The path', el('span', {}, relInfo(c.sig, A, B).short)),
        pathChain(c.p, { noinf: C.noinf, tags: ['Person A', 'Person B'], full: true })))),
      el('p', { class: 'cx-foot muted' }, 'Paths follow PhD and postdoc advisor links; links marked “unverified” are left out. Click “source” on any step to see where it comes from.'));
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
      el('p', {}, 'Data can be wrong or incomplete. Always confirm with the professor’s own CV. ',
        el('button', { class: 'linkbtn inline', type: 'button', onclick: () => openFix(null, 'Other') }, 'Send us a correction or feedback'), '. To fix something about a specific person, open their details and use “Suggest a correction”.'));
  }

  /* ---------- routing ---------- */
  const current = { view: null };
  let lastList = '#/search';
  // 离开结果页前记下滚动位置（换 hash 时浏览器会先滚回顶部）
  function go(id) { if (current.view === 'results') R.scroll = scrollY; location.hash = '#/p/' + encodeURIComponent(id); }
  function show(view) {
    for (const v of ['home', 'results', 'favorites', 'tree', 'connect', 'about']) $('#view-' + v).hidden = v !== view;
    for (const link of document.querySelectorAll('[data-nav]')) {
      link.classList.toggle('active', link.dataset.nav === view);
      if (link.dataset.nav === view) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    current.view = view;
    $('#homeNotice').hidden = true;
    closePanels(); hideTip();
    window.scrollTo(0, 0);
  }
  function route() {
    const h = location.hash || '#/';
    const m = h.match(/^#\/p\/([^?]+)(?:\?(.*))?$/);
    if (m) {
      let id = null;
      try { id = decodeURIComponent(m[1]); } catch (e) { /* 网址里的 %xx 编码坏了 */ }
      if (id != null && G.nodes.has(id)) {
        if (m[2] != null) {
          const q = new URLSearchParams(m[2]);
          F.dist = Math.max(1, Math.min(4, +q.get('within') || 2));
          for (const [k, u] of F_KEYS) F[k] = q.get(u) === '1';
          for (const [k, main] of Object.entries(F_SUB)) if (F[k]) F[main] = true;
        } else if (id !== T.root) {
          resetFam();  // 换到另一个人的树（链接里没带筛选）：筛选回到默认，不把上一个人的筛选带过去
        }
        show('tree'); openTree(id); return;
      }
      // 找不到这个人（链接过期或写错）：回首页并说明一下，而不是悄悄跳走
      show('home');
      document.title = 'Person not found · Academic Lineage Family Tree';
      $('#homeNotice').textContent = 'Person not found. The link may be out of date or mistyped. Try searching by name below.';
      $('#homeNotice').hidden = false;
      return;
    }
    if (h.startsWith('#/search')) {
      const p = new URLSearchParams(h.split('?')[1] || '');
      S.subs = new Set((p.get('r') || '').split(',').map((k) => k.trim()).flatMap((k) => (SUB_LABEL[k] ? [k] : AREAS[k] ? Object.keys(AREAS[k][1]) : [])));
      S.unis = new Set((p.get('i') || '').split(',').map(uniIndex).filter((i) => i != null));
      R.onlyKnown = p.get('known') === '1'; R.onlyAP = p.get('ap') === '1';
      R.rcOpen = p.get('open') === '1'; R.onlyRC = p.get('recruiting') === '1' || R.rcOpen;
      R.sort = SORTS.includes(p.get('sort')) ? p.get('sort') : 'lineage'; $('#sort').value = R.sort;
      $('#onlyKnown').checked = R.onlyKnown; $('#onlyAP').checked = R.onlyAP;
      const back = h === lastList && R.scroll != null;  // 从教授页返回：接着刚才的位置看
      lastList = h;
      if (!back) { R.shown = PAGE; R.scroll = null; }
      show('results'); renderResults();
      if (back) setTimeout(() => scrollTo(0, R.scroll), 0);
      document.title = 'Results · Academic Lineage Family Tree';
      return;
    }
    if (h === '#/connect' || h.startsWith('#/connect?')) {
      const q = new URLSearchParams(h.split('?')[1] || '');
      const ok = (id) => (id && G.nodes.has(id) ? id : null);
      C.a = ok(q.get('a')); C.b = ok(q.get('b')); C.noinf = q.get('noinferred') === '1';
      show('connect'); renderConnect();
      setTimeout(() => { if (!C.a) cxA.input.focus(); else if (!C.b) cxB.input.focus(); }, 0);
      return;
    }
    if (h === '#/favorites') { lastList = h; show('favorites'); renderFavorites(); document.title = 'Favorites · Academic Lineage Family Tree'; return; }
    if (h === '#/about') { show('about'); document.title = 'About · Academic Lineage Family Tree'; return; }
    show('home');
    document.title = 'Academic Lineage Family Tree';
  }

  /* ---------- init ---------- */
  load().then(() => {
    $('#loading').hidden = true;
    for (const box of document.querySelectorAll('[data-search]')) mountSearch(box, box.dataset.search);
    const s = G.summary;
    const stat = (v, l) => el('li', {}, el('b', {}, fmt(v)), el('span', {}, l));
    const cur = G.profs.filter((p) => !p.former), rcAll = rcCounts(cur);
    // 首页数字链到对应的结果页
    const statLink = (v, l, href) => el('li', {}, el('a', { href, class: 'stat-link' }, el('b', {}, fmt(v)), el('span', {}, l)));
    $('#heroStats').replaceChildren(stat(s.current_faculty ?? s.professors, 'professors'), stat(G.unis.length, 'US institutions'),
      stat(s.current_with_advisor ?? s.with_advisor, 'with a known advisor'),
      statLink(rcAll.rc, `say they’re recruiting for ${ADMIT_YEAR}`, '#/search?recruiting=1&sort=recruiting'),
      statLink(rcAll.open, 'more say they’re open to students (no year)', '#/search?recruiting=1&open=1&sort=recruiting'));
    const EXAMPLES = ['Jeffrey Heer', 'Danqi Chen', 'Li Fei-Fei', 'Barbara Liskov'];
    const ex = EXAMPLES.map((nm) => G.profs.find((p) => p.n === nm)).filter(Boolean);
    $('#heroTry').replaceChildren(...(ex.length ? ['Try', ...ex.map((p) => el('a', { href: '#/p/' + encodeURIComponent(p.id) }, p.n))] : []));
    renderAbout();
    mountConnect();
    updateFavCount();
    const resChanged = () => { R.shown = PAGE; renderResults(); lastList = searchHash(); syncHash(lastList); };
    for (const [id, key] of [['onlyKnown', 'onlyKnown'], ['onlyAP', 'onlyAP']])
      $('#' + id).addEventListener('change', (e) => { R[key] = e.target.checked; resChanged(); });
    $('#onlyRC').addEventListener('change', (e) => { R.onlyRC = e.target.checked; if (!R.onlyRC) R.rcOpen = false; resChanged(); });
    $('#onlyRCOpen').addEventListener('change', (e) => { R.rcOpen = e.target.checked; if (R.rcOpen) R.onlyRC = true; resChanged(); });
    for (const b of document.querySelectorAll('.fam-controls [data-dist]')) b.addEventListener('click', () => { F.dist = +b.dataset.dist; renderFamily(); syncHash(famHash()); });
    for (const [id, key] of FAM_TOGGLES)
      $('#' + id).addEventListener('change', (e) => {
        F[key] = e.target.checked;
        if (F_SUB[key] && F[key]) F[F_SUB[key]] = true;
        for (const [sub, main] of Object.entries(F_SUB)) if (main === key && !F[key]) F[sub] = false;
        renderFamily(); syncHash(famHash());
      });
    // “Clear” 按钮清完会隐藏：焦点移到 “Within 2” 上，不丢到页面开头
    $('#famClear').addEventListener('click', () => { resetFam(); renderFamily(); syncHash(famHash()); $('.fam-controls [data-dist="2"]').focus(); });
    $('#onlyRCLabel').textContent = `Recruiting for ${ADMIT_YEAR}`;
    $('#famCopy').addEventListener('click', async () => {
      const b = $('#famCopy');
      try { await navigator.clipboard.writeText(location.href); b.textContent = 'Link copied'; } catch (e) { b.textContent = 'Copy failed'; }
      setTimeout(() => { b.textContent = 'Copy link'; }, 1600);
    });
    $('#favExport').addEventListener('click', exportFavorites);
    for (const b of document.querySelectorAll('[data-favview]')) b.addEventListener('click', () => {
      favView = b.dataset.favview;
      try { localStorage.setItem('alt-fav-view', favView); } catch (e) { /* storage unavailable */ }
      renderFavorites();
    });
    $('#sort').addEventListener('change', (e) => { R.sort = e.target.value; resChanged(); });
    let nt;
    $('#nameFilter').addEventListener('input', (e) => { clearTimeout(nt); nt = setTimeout(() => { R.name = e.target.value; R.shown = PAGE; renderResults(); }, 150); });
    $('#more').addEventListener('click', showMore);
    $('#backBtn').addEventListener('click', () => { location.hash = lastList; });
    $('#genLeft').addEventListener('click', () => { T.lo -= 1; drawTree(); });
    $('#genRight').addEventListener('click', () => { T.lo += 1; drawTree(); });
    const zoom = (d) => { T.zoom = Math.max(0.6, Math.min(1.6, +(T.zoom + d).toFixed(2))); $('#scaleBox').style.transform = `scale(${T.zoom})`; drawTree(); };
    $('#zoomIn').addEventListener('click', () => zoom(0.15));
    $('#zoomOut').addEventListener('click', () => zoom(-0.15));
    $('#resetBtn').addEventListener('click', () => {
      T.zoom = 1; $('#scaleBox').style.transform = '';
      Object.assign(T, { focus: { up: [], down: [] }, page: {}, lo: null, path: null, selected: T.root });
      for (const li of document.querySelectorAll('#famList .fam-row.sel')) li.classList.remove('sel');
      drawTree();
    });
    $('#tabFam').addEventListener('click', () => sidePane('fam'));
    $('.side-tabs').addEventListener('keydown', tabKey);
    $('#bands').addEventListener('keydown', treeKey);
    $('#bands').addEventListener('focusin', (e) => { if (e.target.classList.contains('tnode')) rove(e.target); });
    $('#fixForm').addEventListener('submit', sendFix);
    // 单行输入框里按回车不提交（多半是想换行或打完了），只有点 Send 才发
    $('#fixForm').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') e.preventDefault(); });
    $('#fixSrc').addEventListener('blur', (e) => { e.target.value = fixUrl(e.target.value.trim()); });
    // Esc：写了内容时先确认
    $('#fixDialog').addEventListener('cancel', (e) => { if (!confirmDiscard()) e.preventDefault(); });
    $('#fixClose').addEventListener('click', closeFix);
    $('#fixCancel').addEventListener('click', closeFix);
    // 点背景关闭：按下和松开都在背景上才算（在输入框里拖选文字拖到外面松手，不该关掉表单）
    let downOnBackdrop = false;
    $('#fixDialog').addEventListener('mousedown', (e) => { downOnBackdrop = e.target.id === 'fixDialog'; });
    $('#fixDialog').addEventListener('click', (e) => { if (downOnBackdrop && e.target.id === 'fixDialog') closeFix(); downOnBackdrop = false; });
    for (const a of document.querySelectorAll('[data-fix]')) a.addEventListener('click', (e) => { e.preventDefault(); openFix(null, 'Other'); });
    $('#tabInfo').addEventListener('click', () => sidePane('info'));
    $('#fullBtn').addEventListener('click', () => {
      const shell = $('#treeShell');
      if (document.fullscreenElement) document.exitFullscreen(); else if (shell.requestFullscreen) shell.requestFullscreen();
    });
    document.addEventListener('fullscreenchange', () => setTimeout(drawTree, 60));
    // 手机上地址栏伸缩、滚动时只改高度：不重画（否则树会跳回中间）。宽度变了才重排；
    // 每屏列数没变时保留当前看到的那几代。桌面上只改高度也要重画（每列能放几个人会变）
    let rt, lastW = innerWidth;
    const coarse = matchMedia('(pointer: coarse)');
    addEventListener('resize', () => {
      clearTimeout(rt);
      rt = setTimeout(() => {
        const wChanged = innerWidth !== lastW;
        lastW = innerWidth;
        if (current.view !== 'tree' || (!wChanged && coarse.matches)) return;
        const before = T.n;
        if (wChanged && layoutParams().n !== before) T.lo = null;
        drawTree();
      }, 150);
    });
    addEventListener('hashchange', route);
    route();
  }).catch((err) => {
    $('#loading').replaceChildren(el('span', {}, 'Could not load the data (' + err.message + '). '),
      el('button', { class: 'btn-dark', type: 'button', onclick: () => location.reload() }, 'Try again'));
  });
})();
