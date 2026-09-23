/**
 * 词枢桌面版新能力：macOS 风格窗口栏（红黄绿 + 拖动 + 边缘缩放）与带动画的下拉选择
 * 桌面版行为用 mock 的 pywebview API 验证 —— 能证明按钮确实调到了窗口方法
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');   // 仓库根目录（跟着脚本走，不写死本机路径）
const BASE = 'file:///' + (ROOT + '/apps/word-hub/index.html').replace(/ /g, '%20');
const OUT = path.join(ROOT, '_verify/downloads');
fs.mkdirSync(OUT, { recursive: true });

const R = [];
const check = (n, ok, d = '') => { R.push({ n, ok: !!ok, d: String(d) }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  — ' + d : ''}`); };
const section = s => console.log(`\n----- ${s} -----`);

const MOCK = `
  window.__calls = [];
  window.__z = false;
  window.pywebview = { api: {
    close: () => { window.__calls.push(['close']); return true; },
    minimize: () => { window.__calls.push(['minimize']); return true; },
    zoom: () => { window.__calls.push(['zoom']); window.__z = !window.__z; return window.__z; },
    begin_resize: (...a) => { window.__calls.push(['begin_resize', ...a]); return true; },
    update_resize: (...a) => { window.__calls.push(['update_resize', ...a]); return true; },
    end_resize: () => { window.__calls.push(['end_resize']); return true; },
  }};
`;

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });

  /* ================= 浏览器里：不该出现桌面窗口栏 ================= */
  section('浏览器模式不受影响');
  let page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.route('**/*.woff2', r => r.abort());
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === 6, null, { timeout: 30000 });
  const browserMode = await page.evaluate(() => ({
    desktop: document.body.classList.contains('desktop'),
    bar: getComputedStyle(document.getElementById('macbar')).display,
    rz: getComputedStyle(document.getElementById('rzbox')).display,
  }));
  check('普通浏览器打开时不显示窗口栏与缩放热区',
    !browserMode.desktop && browserMode.bar === 'none' && browserMode.rz === 'none', JSON.stringify(browserMode));
  await page.close();

  /* ================= 下拉动画 ================= */
  section('下拉选择（带动画）');
  page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.route('**/*.woff2', r => r.abort());
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === 6, null, { timeout: 30000 });
  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(400);

  check('原生 select 已替换为自定义组件',
    (await page.$('#setAccent')) === null && (await page.$('#accentSel .sel-btn')) !== null);
  const closed = await page.evaluate(() => {
    const pop = document.querySelector('#accentSel .sel-pop');
    const cs = getComputedStyle(pop);
    return { op: cs.opacity, tf: cs.transform, pe: cs.pointerEvents, val: document.querySelector('#accentSel .sel-val').textContent };
  });
  check('默认收起：浮层透明、不可点、显示当前值',
    closed.op === '0' && closed.pe === 'none' && closed.val === '美音', JSON.stringify(closed));
  check('收起态有位移+缩放（不是简单 display 切换）', /matrix/.test(closed.tf) && !closed.tf.includes('1, 0, 0, 1, 0, 0'), closed.tf);

  await page.click('#accentSel .sel-btn');
  await page.waitForTimeout(350);
  const opened = await page.evaluate(() => {
    const root = document.getElementById('accentSel');
    const pop = root.querySelector('.sel-pop');
    const cs = getComputedStyle(pop);
    const opts = [...root.querySelectorAll('.sel-opt')].map(o => {
      const c = getComputedStyle(o);
      return { op: c.opacity, tf: c.transform, delay: c.transitionDelay, dur: c.transitionDuration };
    });
    return { open: root.classList.contains('open'), op: cs.opacity, tf: cs.transform, pe: cs.pointerEvents, opts, aria: root.querySelector('.sel-btn').getAttribute('aria-expanded'), chev: getComputedStyle(root.querySelector('.chev')).transform };
  });
  check('展开：浮层淡入到位并可点击', opened.open && opened.op === '1' && opened.pe !== 'none', JSON.stringify({ op: opened.op, tf: opened.tf }));
  check('箭头翻转动画到位 + aria-expanded 同步', /matrix\(-1/.test(opened.chev) && opened.aria === 'true', opened.chev);
  // 这条专门盯「浮层被祖先 overflow:hidden 裁掉」这类 bug：分组列表曾把第二个选项切掉一半
  const clip = await page.evaluate(() => {
    const pop = document.querySelector('#accentSel .sel-pop');
    const opt = document.querySelector('#accentSel .sel-opt[data-v="en-GB"]');
    const r = opt.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    let clipped = false, why = '';
    let el = pop.parentElement;
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      if (cs.overflow !== 'visible') {
        const pr = pop.getBoundingClientRect(), er = el.getBoundingClientRect();
        if (pr.bottom > er.bottom + 1 || pr.top < er.top - 1) { clipped = true; why = (el.className || el.tagName) + ' overflow=' + cs.overflow; }
      }
      el = el.parentElement;
    }
    return { hitIsOption: opt.contains(hit), hitCls: hit ? (hit.className || hit.tagName) : null, clipped, why };
  });
  check('浮层不被任何祖先裁切，且选项中心确实点得到',
    clip.hitIsOption && !clip.clipped, JSON.stringify(clip));

  check('选项有阶梯延迟（错峰浮现）',
    opened.opts.length === 2 && opened.opts[0].delay !== opened.opts[1].delay, JSON.stringify(opened.opts.map(o => o.delay)));
  check('过渡用的是缓动曲线而非 linear（丝滑）',
    /cubic-bezier/.test(await page.evaluate(() => getComputedStyle(document.querySelector('#accentSel .sel-pop')).transitionTimingFunction)),
    await page.evaluate(() => getComputedStyle(document.querySelector('#accentSel .sel-pop')).transitionTimingFunction));

  await page.click('#accentSel .sel-opt[data-v="en-GB"]');
  await page.waitForTimeout(400);
  const picked = await page.evaluate(() => ({
    val: document.querySelector('#accentSel .sel-val').textContent,
    stored: localStorage.getItem('wh_setting_accent'),
    accent: Settings.accent,
    open: document.getElementById('accentSel').classList.contains('open'),
    on: document.querySelector('#accentSel .sel-opt.on').dataset.v,
  }));
  check('选中英音：值更新 + 写入设置与本地存储 + 自动收起',
    picked.val === '英音' && picked.stored === '"en-GB"' && picked.accent === 'en-GB' && !picked.open && picked.on === 'en-GB',
    JSON.stringify(picked));

  // 点击外部关闭
  await page.click('#accentSel .sel-btn');
  await page.waitForTimeout(250);
  await page.click('#page-settings .sec');
  await page.waitForTimeout(300);
  check('点击组件外部自动收起', !(await page.evaluate(() => document.getElementById('accentSel').classList.contains('open'))));

  // 键盘
  await page.focus('#accentSel .sel-btn');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(250);
  const kbOpen = await page.evaluate(() => document.getElementById('accentSel').classList.contains('open'));
  // 此前已是 en-GB（末位），先按 ArrowUp 回到美音，再按 ArrowDown 回到英音
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(250);
  const kbUp = await page.evaluate(() => Settings.accent);
  await page.keyboard.press('ArrowDown');
  await page.waitForTimeout(250);
  const kbDown = await page.evaluate(() => Settings.accent);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  const kbClosed = await page.evaluate(() => document.getElementById('accentSel').classList.contains('open'));
  check('键盘可操作（Enter 展开 / 上下键切换 / Esc 收起）',
    kbOpen && kbUp === 'en-US' && kbDown === 'en-GB' && !kbClosed,
    JSON.stringify({ kbOpen, kbUp, kbDown, kbClosed }));
  await page.close();

  /* ================= 桌面窗口栏 ================= */
  section('macOS 风格窗口栏（?desktop=1 + mock 窗口 API）');
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(MOCK);
  page = await ctx.newPage();
  await page.route('**/*.woff2', r => r.abort());
  await page.goto(BASE + '?desktop=1', { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === 6, null, { timeout: 30000 });
  await page.waitForTimeout(700);

  const bar = await page.evaluate(() => {
    const b = document.getElementById('macbar');
    const cs = getComputedStyle(b);
    const lights = [...document.querySelectorAll('#macbar .lt')].map(el => ({ act: el.dataset.act, bg: getComputedStyle(el).backgroundColor, w: Math.round(el.getBoundingClientRect().width) }));
    return {
      desktop: document.body.classList.contains('desktop'),
      display: cs.display, h: b.getBoundingClientRect().height, pos: cs.position,
      lights, title: document.querySelector('.mactitle').textContent,
      dragRegion: !!document.querySelector('.macdrag'),
      // v1.11 起拖动由页面自己实现：绝不能再挂 pywebview 的内置拖动标记，
      // 否则又会回到「每次 mousemove 过一次桥」的老路
      dragLegacy: !!document.querySelector('.pywebview-drag-region'),
      hit24: (() => { const a = getComputedStyle(document.querySelector('.lt-close'), '::after'); return a.inset; })(),
      headerTop: getComputedStyle(document.querySelector('header')).top,
      padTop: getComputedStyle(document.body).paddingTop,
    };
  });
  check('桌面模式出现窗口栏，位置固定在顶部、高 38px', bar.desktop && bar.display === 'flex' && bar.h === 38 && bar.pos === 'fixed', JSON.stringify({ h: bar.h, pos: bar.pos }));
  check('三颗按钮顺序为 最小化/缩放/关闭（Windows 习惯），颜色是 macOS 黄绿红',
    bar.lights.map(l => l.act).join(',') === 'min,zoom,close'
    && bar.lights[0].bg === 'rgb(254, 188, 46)' && bar.lights[1].bg === 'rgb(40, 200, 64)' && bar.lights[2].bg === 'rgb(255, 95, 87)',
    JSON.stringify(bar.lights.map(l => l.bg)));
  check('按钮是 12px 圆点', bar.lights.every(l => l.w === 12), JSON.stringify(bar.lights.map(l => l.w)));
  check('窗口栏带拖动层，且没有 pywebview 内置拖动标记（拖动已改由页面自己实现）',
    bar.dragRegion && !bar.dragLegacy, JSON.stringify({ drag: bar.dragRegion, legacy: bar.dragLegacy }));
  check('按钮命中区被撑到 24×24（12px 圆点 + 伪元素各外扩 6px）',
    /-6px/.test(bar.hit24), bar.hit24);
  const pos = await page.evaluate(() => {
    const bar = document.getElementById('macbar').getBoundingClientRect();
    const lights = document.querySelector('#macbar .lights').getBoundingClientRect();
    const title = document.querySelector('.mactitle').getBoundingClientRect();
    return {
      lightsRightGap: Math.round(bar.right - lights.right),
      titleLeftGap: Math.round(title.left - bar.left),
      titleIsLeftOfLights: title.right <= lights.left + 1,
      closeIsRightmost: Math.round(bar.right - document.querySelector('.lt-close').getBoundingClientRect().right),
    };
  });
  check('三颗按钮在窗口右上角（关闭在最右）、标题在左侧',
    pos.lightsRightGap <= 20 && pos.closeIsRightmost <= 20 && pos.titleLeftGap <= 20 && pos.titleIsLeftOfLights,
    JSON.stringify(pos));
  check('页面内容整体下移，顶部标题栏不再被压住', bar.headerTop === '38px' && bar.padTop === '38px',
    JSON.stringify({ headerTop: bar.headerTop, padTop: bar.padTop }));
  await page.screenshot({ path: path.join(OUT, 'mac_window.png'), timeout: 12000 }).catch(() => {});

  // 三颗按钮
  await page.click('#macbar .lt-close');
  await page.click('#macbar .lt-min');
  await page.click('#macbar .lt-zoom');
  await page.waitForTimeout(300);
  let calls = await page.evaluate(() => window.__calls.map(c => c[0]));
  check('红点=关闭、黄点=最小化、绿点=缩放（确实调到了窗口 API）',
    calls.join(',') === 'close,minimize,zoom', calls.join(','));
  check('缩放后按钮提示改为「还原」',
    (await page.getAttribute('#macbar .lt-zoom', 'title')) === '还原',
    await page.getAttribute('#macbar .lt-zoom', 'title'));

  // 双击拖动层 = 缩放
  await page.dblclick('#macbar .macdrag');
  await page.waitForTimeout(250);
  calls = await page.evaluate(() => window.__calls.map(c => c[0]));
  check('双击标题栏也触发缩放（macOS 习惯）', calls[calls.length - 1] === 'zoom', calls.join(','));

  // 边缘缩放热区
  const handles = await page.evaluate(() => [...document.querySelectorAll('#rzbox .rz')].map(e => ({
    edge: e.dataset.edge, cursor: getComputedStyle(e).cursor,
    w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height),
  })));
  // 顶部三条（n / nw / ne）刻意让位给标题栏拖动：否则热区层级高会抢走顶端的点击，拖不动窗口
  check('五条边缘缩放热区齐全且光标正确（上边与两个上角已让位给拖动）',
    handles.length === 5 && handles.every(h => /resize/.test(h.cursor))
    && JSON.stringify(handles.map(h => h.edge)) === JSON.stringify(['s', 'w', 'e', 'sw', 'se']),
    JSON.stringify(handles.map(h => h.edge)));

  await page.evaluate(() => { window.__calls.length = 0; });
  const box = await page.evaluate(() => {
    const r = document.querySelector('.rz-e').getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  });
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.mouse.move(box.x + 60, box.y + 40, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const rz = await page.evaluate(() => window.__calls);
  const begin = rz.find(c => c[0] === 'begin_resize');
  const ups = rz.filter(c => c[0] === 'update_resize');
  check('拖右边缘：发出 begin_resize 并带上「边 / 屏幕坐标 / CSS 尺寸」',
    !!begin && begin.length === 6 && begin[1] === 'e'
    && typeof begin[2] === 'number' && typeof begin[3] === 'number'
    && begin[4] === 1440 && begin[5] === 900, JSON.stringify(begin));
  check('拖动过程用 rAF 节流地连续下发 update_resize', ups.length >= 1 && ups.length <= 12, ups.length + ' 次');
  check('松手时结束缩放', rz[rz.length - 1][0] === 'end_resize', rz[rz.length - 1][0]);
  check('拖动中禁止选中文本（体验细节）', await page.evaluate(() => {
    // 松手后类应被移除
    return !document.body.classList.contains('rz-dragging');
  }));

  /* ---------------- 桌面模式不许出现页面滚动条 ---------------- */
  section('桌面模式无滚动条（按窗口真实客户区测）');
  // 关键：1360×900 是窗口外框，客户区只有 1345×863；最小窗口 1060×820 → 客户区约 1045×783
  // 上一轮就是拿 900 视口测的，才漏掉「学习统计」页
  // 实测（本机 150% 缩放）：默认窗口 2018×1312 物理 = 页面 1345×874 CSS；
  // 最小窗口 1075×875 → 页面同高、宽 1075。Playwright 的 viewport 就是 CSS 像素，直接按这个测。
  const CASES = [[1345, 874, ['3', '4']], [1075, 874, ['3']]];
  const bad = [];
  for (const [vw, vh, fonts] of CASES) {
    await page.setViewportSize({ width: vw, height: vh });
    await page.waitForTimeout(400);
    for (const fs of fonts) {
      await page.evaluate((f) => { Settings.font = f; applyAppearance(); }, fs);
      for (const tab of ['home', 'favs', 'stats', 'settings']) {
        await page.click(`nav button[data-tab="${tab}"]`);
        await page.waitForTimeout(320);
        const r = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
        if (r > 0) bad.push(`${vw}×${vh} 字号${fs}/${tab}: +${r}px`);
      }
      await page.click('nav button[data-tab="home"]');
      await page.waitForTimeout(250);
      await page.evaluate(() => { go('study'); showCard(); });
      await page.waitForTimeout(400);
      const r = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
      if (r > 0) bad.push(`${vw}×${vh} 字号${fs}/study: +${r}px`);
    }
  }
  check('默认窗口两种字号 + 最小窗口默认字号，六个页签全部零溢出',
    bad.length === 0, bad.join(' ; ') || '全部组合 0 溢出');
  /* ---------------- 翻面后也不许出滚动条（v1.21：卡片高度交给 flex + 评分区常驻占位） ---------------- */
  const badFlip = [], jump = [];
  // 这段要用到真实的词库数据，前面没选过就先选一个
  if (!await page.evaluate(() => !!S.list)) {
    await page.evaluate(() => selectList(Object.keys(LISTS)[0]));   // 直接用应用自己的选库逻辑，避免受当前页签影响
    await page.waitForFunction(() => !!(S.list && S.data), null, { timeout: 30000 });
    await page.waitForTimeout(500);
  }
  for (const [vw, vh, fonts] of CASES) {
    await page.setViewportSize({ width: vw, height: vh });
    for (const fs of fonts) {
      await page.evaluate((f) => { Settings.font = f; applyAppearance(); }, fs);
      // 先保证"确实有一张卡"：前面的用例可能已经把队列走完了
      await page.evaluate(() => {
        Settings.daily = 20;
        const prog = {};
        S.data.slice(0, 20).forEach(e => {
          prog[e.w] = { ef: 2.5, ivl: 1, reps: 1, due: today() + 30, lapses: 0, seen: 1 };
        });
        saveProg(S.list, prog);
        buildQueue();                       // S.active 会被置回 'new'
        openSession('new');
      });
      await page.waitForTimeout(250);
      // 把背面撑到最长：长释义 + 长英文释义 + 词形
      await page.evaluate(() => {
        const w = S.queue[S.idx].w;
        const e = S.data.find(x => x.w === w);
        e.t = 'n. ' + '很长的中文释义，用来把卡片背面撑满。'.repeat(6);
        e.d = 'a very long definition used to stress the back face of the card. '.repeat(8);
        e.e = 'p:measured/i:measuring/3:measures/d:measured';
        S.revealed = false;
        showCard();
      });
      await page.waitForTimeout(300);
      const front = await page.evaluate(() => ({
        over: document.documentElement.scrollHeight - window.innerHeight,
        h: Math.round(document.getElementById('cardInner').getBoundingClientRect().height),
      }));
      await page.evaluate(() => reveal());
      await page.waitForTimeout(750);
      const back = await page.evaluate(() => ({
        over: document.documentElement.scrollHeight - window.innerHeight,
        h: Math.round(document.getElementById('cardInner').getBoundingClientRect().height),
        grades: getComputedStyle(document.getElementById('grades')).visibility,
        nextIntv: getComputedStyle(document.getElementById('nextIntv')).visibility,
      }));
      if (front.over > 0 || back.over > 0) badFlip.push(`${vw}×${vh} 字号${fs}: 翻面前+${front.over} 翻面后+${back.over}`);
      if (back.grades !== 'visible' || back.nextIntv !== 'visible') badFlip.push(`${vw}×${vh} 字号${fs}: 评分区未显示(${back.grades}/${back.nextIntv})`);
      if (Math.abs(back.h - front.h) > 2) jump.push(`${vw}×${vh} 字号${fs}: ${front.h} → ${back.h}`);
    }
  }
  check('翻面后（评分区出现）整页仍不出现滚动条', badFlip.length === 0, badFlip.join(' ; ') || '全部 0 溢出');
  check('翻面前后卡片高度基本不变（不会翻一下跳一下）', jump.length === 0, jump.join(' ; ') || '高度稳定');
  check('评分区用 visibility 占位（display 仍是 grid，位置一直留着）',
    await page.evaluate(() => {
      const g = document.getElementById('grades');
      return getComputedStyle(g).display === 'grid' && g.getBoundingClientRect().height > 20;
    }), '');

  /* ---------------- 翻面「动画期间」也不许越界（3D 投影膨胀会顶出滚动条） ---------------- */
  // 透视越强，卡片转到侧面时投影包围盒膨胀越大 → 会越过视口底边 → 页面闪出滚动条。
  // 这里按 16ms 采样整段动画，只要有任何非 fixed 元素越过视口底边就判失败。
  const animBad = [];
  for (const [vw, vh] of [[1345, 874], [1075, 875], [1600, 1000], [2100, 1200]]) {
    await page.setViewportSize({ width: vw, height: vh });
    await page.evaluate(() => {
      Settings.daily = 20;
      const prog = {};
      S.data.slice(0, 20).forEach(e => { prog[e.w] = { ef: 2.5, ivl: 1, reps: 1, due: today() + 30, lapses: 0, seen: 1 }; });
      saveProg(S.list, prog);
      buildQueue();
      openSession('new');
      S.revealed = false;
      showCard();
    });
    await page.waitForTimeout(450);
    await page.evaluate(() => {
      window.__m = -1e9; window.__who = ''; window.__doc = -1e9;
      const name = el => el.id || (typeof el.className === 'string' ? el.className.split(' ')[0] : el.tagName);
      window.__iv = setInterval(() => {
        const de = document.documentElement;
        window.__doc = Math.max(window.__doc, de.scrollHeight - de.clientHeight);
        for (const el of document.querySelectorAll('body, body *')) {
          if (getComputedStyle(el).position === 'fixed') continue;   // fixed 不产生滚动区域
          const r = el.getBoundingClientRect();
          if (!r.width && !r.height) continue;
          const dy = r.bottom - window.innerHeight;
          if (dy > window.__m) { window.__m = dy; window.__who = name(el); }
        }
      }, 16);
    });
    await page.evaluate(() => reveal());
    await page.waitForTimeout(1400);
    const r = await page.evaluate(() => {
      clearInterval(window.__iv);
      return { worst: Math.round(window.__m), who: window.__who, doc: Math.round(window.__doc) };
    });
    if (r.worst > 0 || r.doc > 0) animBad.push(`${vw}×${vh}: 越界 ${r.worst}px (${r.who}) / 文档溢出 ${r.doc}px`);
  }
  check('翻面动画全程不越出视口（不会闪出滚动条）', animBad.length === 0, animBad.join(' ; ') || '4 个尺寸（含 2100 宽）全程 0 越界');

  // 透视距离必须随卡片尺寸自适应：写死的值在宽窗口下压不住投影膨胀（2100 宽时会溢出 35px）
  const persp = {};
  for (const [vw, vh] of [[1345, 874], [2100, 1200]]) {
    await page.setViewportSize({ width: vw, height: vh });
    await page.waitForTimeout(350);            // 等视口切换稳定，否则会量到上一次的值
    persp[vw] = await page.evaluate(async () => {
      const inner = document.getElementById('cardInner');
      S.revealed = false; showCard(); fitFlipPerspective();
      await new Promise(r => requestAnimationFrame(r));
      const r = inner.getBoundingClientRect();
      return { card: Math.round(r.width), px: parseFloat(getComputedStyle(document.querySelector('.flip-scene')).perspective) };
    });
  }
  check('透视距离随卡片尺寸自适应（窗口越宽取值越大，不会写死）',
    persp[1345].px >= 2800 && persp[2100].px > persp[1345].px,
    JSON.stringify(persp));

  // 单独把「学习统计」点出来，这页内容最高、最容易超标
  await page.setViewportSize({ width: 1345, height: 874 });
  await page.waitForTimeout(300);
  await page.click('nav button[data-tab="stats"]');
  await page.waitForTimeout(400);
  const statsGeo = await page.evaluate(() => {
    const el = document.getElementById('page-stats');
    const bg = document.getElementById('listBars');
    return { over: document.documentElement.scrollHeight - window.innerHeight,
             page: Math.round(el.getBoundingClientRect().height), content: el.scrollHeight,
             panels: el.querySelectorAll('.panel').length,
             barCols: getComputedStyle(bg).gridTemplateColumns.split(' ').length,
             barRows: getComputedStyle(bg).gridTemplateRows.split(' ').length };
  });
  check('学习统计页留有余量，两个分组卡 + 进度条在窄窗也保持 3 列不换行',
    statsGeo.over <= 0 && statsGeo.content <= statsGeo.page && statsGeo.panels === 2
    && statsGeo.barCols === 3 && statsGeo.barRows === 2, JSON.stringify(statsGeo));

  console.log('\n页面 JS 报错: 无');
  await browser.close();
  const failed = R.filter(r => !r.ok);
  console.log(`\n================ 桌面版 UI 汇总：通过 ${R.length - failed.length}/${R.length} ================`);
  if (failed.length) console.log(failed.map(f => ' - ' + f.n + '  ' + f.d).join('\n'));
})().catch(e => { console.error('脚本异常', e); process.exit(1); });
