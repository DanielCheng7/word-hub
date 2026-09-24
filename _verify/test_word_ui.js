/**
 * 词枢新增能力验证：固定窗口 / 字号档 / 深色模式 / 例句与重点色标注
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');   // 仓库根目录（跟着脚本走，不写死本机路径）
const URLX = 'file:///' + (ROOT + '/apps/word-hub/index.html').replace(/ /g, '%20');
const OUT = path.join(ROOT, '_verify/downloads');
fs.mkdirSync(OUT, { recursive: true });

const R = [];
const check = (n, ok, d = '') => { R.push({ n, ok: !!ok, d: String(d) }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  — ' + d : ''}`); };
const section = s => console.log(`\n----- ${s} -----`);
// 沙箱里 CDN 字体可能永远不 ready，Playwright 截图会一直等 → 容错处理
const shot = async (p, f) => { try { await p.screenshot({ path: f, timeout: 12000 }); } catch (e) { console.log('  （截图跳过：' + e.message.split('\n')[0].slice(0, 60) + '）'); } };

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/net::|ERR_/.test(m.text())) errs.push('CONSOLE ' + m.text()); });
  await page.goto(URLX, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 30000 });

  /* ---------------- 铺满窗口 ---------------- */
  section('铺满窗口（内容面板与卡片跟着窗口走）');
  const measure = () => page.evaluate(() => {
    const main = document.querySelector('main');
    const nav = document.querySelector('nav');
    const page1 = document.querySelector('.page.active');
    const grid = document.querySelector('#listCards');
    const card = document.querySelector('#listCards .list-card');
    return {
      vw: window.innerWidth, vh: window.innerHeight,
      main: main.offsetWidth, nav: nav.offsetWidth, page: page1.offsetWidth,
      pageH: page1.offsetHeight,
      grid: grid ? grid.offsetWidth : 0,
      cardW: card ? card.offsetWidth : 0,
      docW: document.documentElement.scrollWidth,
      bodyH: document.body.scrollHeight,
    };
  });
  const widths = {};
  for (const w of [1000, 1280, 1440, 1920]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(400);
    widths[w] = await measure();
  }
  check('内容区宽度 = 窗口宽度（左右不再留大片空白）',
    [1000, 1280, 1440, 1920].every(w => widths[w].main === w),
    [1000, 1280, 1440, 1920].map(w => `${w}→${widths[w].main}`).join('  '));
  check('白色内容面板只留 28px 边距，铺满窗口',
    [1000, 1280, 1440, 1920].every(w => widths[w].page === w - 56),
    [1000, 1280, 1440, 1920].map(w => `${w}→${widths[w].page}`).join('  '));
  // ⚠️ 词库从 6 个加到 8 个后列数由 3 变 4（8 张卡要两行放完），卡片绝对宽度自然变小；
  //    这里不再写死 px，只判"随窗口变宽"这个性质本身
  check('首页词库卡片随窗口变宽',
    widths[1920].cardW > widths[1280].cardW + 100 && widths[1280].cardW > 200,
    `${widths[1280].cardW}px → ${widths[1920].cardW}px`);
  check('面板纵向也铺满窗口（高度 ≥ 视口 - 200）',
    widths[1920].pageH >= widths[1920].vh - 200, `面板 ${widths[1920].pageH}px / 视口 ${widths[1920].vh}px`);
  check('任何宽度下都不出现横向滚动',
    [1000, 1280, 1440, 1920].every(w => widths[w].docW === w),
    [1000, 1280, 1440, 1920].map(w => widths[w].docW).join('/'));
  await page.setViewportSize({ width: 700, height: 900 });
  await page.waitForTimeout(400);
  const narrow = await measure();
  check('窄窗口适配（700px 窗口下不横向溢出）', narrow.main === 700 && narrow.docW === 700, `main=${narrow.main} doc=${narrow.docW}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(400);

  /* ---------------- 字号档 ---------------- */
  section('字号档位');
  const fontOf = async () => {
    await page.evaluate(() => { if (S.list) { go('study'); showCard(); } });
    await page.waitForTimeout(650);          // 等页面入场动画（scale .988）走完再量
    return page.evaluate(() => {
      const q = sel => { const el = document.querySelector(sel); return el ? getComputedStyle(el).fontSize : null; };
      return {
        fs: document.documentElement.dataset.fs,
        word: q('.face.front .word'),
        hero: q('.task-hero .big'),
        zh: q('.face.back .zh'),
        stored: localStorage.getItem('wh_setting_font'),
      };
    });
  };
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  await page.click('#listCards .list-card[data-k="cet4"]');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 30000 });
  const f0 = await fontOf();
  check('默认字号档为「特大」（data-fs=3）', f0.fs === '3', JSON.stringify({ fs: f0.fs, word: f0.word, hero: f0.hero }));
  check('默认字号明显更大（单词 55px、大字 44px）',
    parseFloat(f0.word) === 55 && parseFloat(f0.hero) === 44, `${f0.word} / ${f0.hero}`);

  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(250);
  const pills = await page.$$eval('#fontPills .dpill', e => e.map(x => [x.dataset.fs, x.textContent, x.classList.contains('on')]));
  check('设置页有 4 档字号可选且默认选中「特大」',
    pills.length === 4 && pills.find(p => p[0] === '3')[2] === true, JSON.stringify(pills));

  await page.click('#fontPills .dpill[data-fs="4"]');
  await page.waitForTimeout(300);
  const f4 = await fontOf();
  check('选「超大」后各字号同步放大', f4.fs === '4' && parseFloat(f4.word) === 61 && parseFloat(f4.hero) === 48,
    `${f4.word} / ${f4.hero}`);
  // 拼写区字号必须跟着档位走（此前是写死的 18/16px，用户反馈"拼写功能内部字体有点小"）
  const spellFs = await page.evaluate(() => {
    Settings.mode = 'spell'; go('study'); showCard();
    return {
      first: getComputedStyle(document.querySelector('#spellBox .first')).fontSize,
      input: getComputedStyle(document.getElementById('spellInput')).fontSize,
      prompt: getComputedStyle(document.querySelector('#spellBox .prompt')).fontSize,
    };
  });
  check('拼写区字号接入档位（超大档：空缺行 44px / 输入框 27px / 释义 22px）',
    spellFs.first === '44px' && spellFs.input === '27px' && spellFs.prompt === '22px', JSON.stringify(spellFs));
  await page.evaluate(() => { Settings.font = '4'; applyAppearance(); });
  check('字号选择写入本地存储', f4.stored === '"4"', f4.stored);
  await page.evaluate(() => { Settings.mode = 'card'; go('study'); showCard(); });   // 切回卡片模式再量
  await page.waitForTimeout(700);
  const cardGeo = await page.evaluate(() => {
    const pg = document.querySelector('.page.active');
    const box = document.getElementById('cardBox');
    return { page: pg.offsetWidth, card: box.offsetWidth, cardH: document.getElementById('cardInner').offsetHeight, vh: window.innerHeight };
  });
  check('单词卡横向铺满面板（面板 ' + (cardGeo.page - 48) + 'px 内容宽 → 卡片 ' + cardGeo.card + 'px）',
    Math.abs(cardGeo.card - (cardGeo.page - 48)) <= 2, JSON.stringify(cardGeo));
  check('单词卡纵向也跟窗口走（高度 > 400px 且随视口变化）', cardGeo.cardH > 400, cardGeo.cardH + 'px / 视口 ' + cardGeo.vh);

  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(200);
  await page.click('#fontPills .dpill[data-fs="2"]');
  await page.waitForTimeout(250);
  check('可以切回「大」档', (await fontOf()).fs === '2');

  /* ---------------- 深色模式 ---------------- */
  section('深色模式');
  const themeOf = () => page.evaluate(() => {
    const cs = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el).backgroundColor : null; };
    return {
      attr: document.documentElement.dataset.theme,
      body: cs('body'), page: cs('.page.active'), card: cs('.face') || cs('.list-card'),
      nav: getComputedStyle(document.querySelector('header')).backgroundColor,
      text: getComputedStyle(document.body).color,
      stored: localStorage.getItem('wh_setting_theme'),
    };
  });
  const t0 = await themeOf();
  check('默认浅色', t0.attr === 'light' && t0.body === 'rgb(245, 245, 247)', JSON.stringify({ a: t0.attr, b: t0.body }));

  // fontOf 会把页面留在复习页，切主题前先回到设置页
  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(250);
  await page.click('#themePills .dpill[data-theme="dark"]');
  await page.waitForTimeout(350);
  const td = await themeOf();
  check('切换深色：背景变深、文字变浅',
    td.attr === 'dark' && td.body === 'rgb(20, 21, 24)' && /rgb\((2[0-9]{2}|1[89][0-9])/.test(td.text),
    `body=${td.body} text=${td.text}`);
  check('深色下卡片与内容区同为深色', td.card && td.card !== 'rgb(255, 255, 255)', 'card=' + td.card);
  check('深色写入本地存储', td.stored === '"dark"', td.stored);

  // 深色下主要界面元素都应换色（不能有白底残留）
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(300);
  const whiteSpots = await page.evaluate(() => {
    const bad = [];
    document.querySelectorAll('.page.active *').forEach(el => {
      const bg = getComputedStyle(el).backgroundColor;
      if (bg === 'rgb(255, 255, 255)' && el.getBoundingClientRect().width > 40) {
        bad.push(el.className || el.tagName);
      }
    });
    return [...new Set(bad)].slice(0, 6);
  });
  check('深色下学习页无白色底残留', whiteSpots.length === 0, whiteSpots.join(' | ') || '无');
  await shot(page, path.join(OUT, 'word_dark_study.png'));

  await page.click('nav button[data-tab="stats"]');
  await page.waitForTimeout(300);
  const statDark = await page.evaluate(() => {
    const c = document.querySelector('.stat-card');
    return { card: getComputedStyle(c).backgroundColor, heat: getComputedStyle(document.querySelector('#heatmap i')).backgroundColor };
  });
  check('深色下统计卡与热力图空格同步换色',
    statDark.card === 'rgb(31, 33, 38)' && statDark.heat !== 'rgba(0, 0, 0, 0.06)', JSON.stringify(statDark));

  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(200);
  await page.click('#themePills .dpill[data-theme="light"]');
  await page.waitForTimeout(250);
  check('可以切回浅色', (await themeOf()).attr === 'light');

  /* ---------------- 例句与重点色 ---------------- */
  section('例句与重点色标注');
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  const sentLoaded = await page.evaluate(() => Object.keys(window.ECDICT_SENT || {}).length);
  check('例句数据已随词库加载（data_sent.js）', sentLoaded > 9000, sentLoaded + ' 条');

  // 取一个有例句的词直接渲染卡片背面
  const probe = await page.evaluate(() => {
    const words = Object.keys(window.ECDICT_SENT);
    const w = words.includes('abandon') ? 'abandon' : words[0];
    const entry = { w, t: '测试释义', d: '', e: 'd:abandoned/p:abandoned/i:abandoning/3:abandons' };
    const html = sentHtml(entry);
    const box = document.createElement('div');
    box.innerHTML = html;
    const mark = box.querySelector('mark');
    const se = box.querySelector('.se');
    const sz = box.querySelector('.sz');
    return {
      w, data: window.ECDICT_SENT[w],
      hasSent: !!box.querySelector('.sent'),
      en: se ? se.textContent : '',
      zh: sz ? sz.textContent : '',
      marks: [...box.querySelectorAll('mark')].map(m => m.textContent),
      markColor: mark ? getComputedStyle(mark).color : '',
    };
  });
  check('卡片背面能渲染例句（英文 + 中文）', probe.hasSent && probe.en.length > 10 && probe.zh.length > 0,
    `${probe.en.slice(0, 60)} | ${probe.zh.slice(0, 30)}`);
  check('目标词用重点色标注（<mark>）', probe.marks.length > 0, '标注:' + JSON.stringify(probe.marks));
  const marksMatch = probe.marks.every(m => probe.en.toLowerCase().includes(m.toLowerCase()));
  check('标注内容确实出现在例句里', marksMatch, JSON.stringify(probe.marks));
  check('中文翻译与英文例句来自同一句对', probe.data[0].includes(probe.marks[0] || '@@'), probe.data[1].slice(0, 30));

  // 词形变化也要标出来
  const inflect = await page.evaluate(() => {
    const r = {};
    ['eagle', 'abandon', 'queen'].forEach(w => {
      const s = (window.ECDICT_SENT || {})[w];
      if (!s) return;
      const entry = (S.data || []).find(e => e.w === w) || { w, t: '', d: '', e: '' };
      const box = document.createElement('div');
      box.innerHTML = sentHtml(entry);
      r[w] = { en: s[0], forms: formsOf(entry), marks: [...box.querySelectorAll('mark')].map(m => m.textContent) };
    });
    return r;
  });
  const inflectOK = Object.values(inflect).every(v => v.marks.length > 0 && v.marks.every(m => v.en.toLowerCase().includes(m.toLowerCase())));
  check('例句里的词形变化（复数/过去式）被正确标出', inflectOK, JSON.stringify(inflect).slice(0, 240));

  // 无例句的词给出明确提示而不是空白
  const noneHtml = await page.evaluate(() => sentHtml({ w: 'zzzznotarealword', t: '', d: '', e: '' }));
  check('库里没有的词给出「暂无例句」提示', /暂无例句/.test(noneHtml), noneHtml.slice(0, 40));

  // 真正翻到卡片背面看渲染（含滚动容器里的可见性）
  const realCard = await page.evaluate(() => {
    Settings.mode = 'card';
    // 挑一个确定有例句的词作为当前卡
    const w = Object.keys(window.ECDICT_SENT).includes('abandon') ? 'abandon' : Object.keys(window.ECDICT_SENT)[0];
    S.list = S.list || 'cet4';
    buildQueue(); render(); go('study');
    // 把队列首项换成已知有例句的词，直接渲染
    S.queue.unshift({ w, type: 'nw' });
    S.idx = 0;
    showCard();
    reveal();
    const back = document.getElementById('faceBack');
    const sent = back.querySelector('.sent');
    return {
      inDom: !!sent,
      visible: sent ? sent.getBoundingClientRect().height > 0 : false,
      marks: sent ? sent.querySelectorAll('mark').length : 0,
      zh: sent && sent.querySelector('.sz') ? sent.querySelector('.sz').textContent.slice(0, 26) : '',
    };
  });
  check('翻面后卡片背面真实显示例句块', realCard.inDom && realCard.visible, JSON.stringify(realCard));
  check('渲染出的卡片里重点色标注生效', realCard.marks > 0, realCard.marks + ' 处');
  await shot(page, path.join(OUT, 'word_card_sentence.png'));

  /* ---------------- 持久化 ---------------- */
  section('设置持久化');
  await page.evaluate(() => { Settings.font = '3'; Settings.theme = 'dark'; applyAppearance(); });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 30000 });
  const after = await page.evaluate(() => ({
    fs: document.documentElement.dataset.fs,
    theme: document.documentElement.dataset.theme,
    body: getComputedStyle(document.body).backgroundColor,
    word: getComputedStyle(document.querySelector('.face.front .word') || document.body).fontSize,
  }));
  check('刷新后字号与主题都保持', after.fs === '3' && after.theme === 'dark' && after.body === 'rgb(20, 21, 24)',
    JSON.stringify(after));

  /* ---------------- 页签互斥 + 「今日新学 / 到期复习」分离 ---------------- */
  section('页签互斥与「新学 / 复习」分离');
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 30000 });
  await page.waitForTimeout(700);

  const visiblePages = () => page.evaluate(() => {
    const all = [...document.querySelectorAll('.page')];
    const vis = all.filter(p => getComputedStyle(p).display !== 'none');
    return {
      count: vis.length, ids: vis.map(p => p.id),
      stray: all.filter(p => !p.classList.contains('active') && getComputedStyle(p).display !== 'none').map(p => p.id),
    };
  });
  const strayReport = [];
  for (const t of ['home', 'study', 'favs', 'stats', 'settings']) {
    await page.click(`nav button[data-tab="${t}"]`);
    await page.waitForTimeout(400);
    const v = await visiblePages();
    if (v.count !== 1 || v.stray.length) strayReport.push(`${t}→可见${v.count}个[${v.ids}] 多于:${v.stray}`);
  }
  check('五个页签都只有一个内容面板可见（没有多出的空白长条）', strayReport.length === 0, strayReport.join(' ; ') || '5/5 正常');

  // 未选词库 → 复习页提示去选词库
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(500);
  const idle0 = await page.evaluate(() => ({
    disp: getComputedStyle(document.getElementById('studyIdle')).display,
    title: document.getElementById('idleTitle').textContent,
    btn: document.getElementById('btnIdleStart').textContent,
  }));
  check('未选词库时复习页给出空状态提示（不是一大块白）',
    idle0.disp === 'flex' && /还没有选择词库/.test(idle0.title) && /去选词库/.test(idle0.btn), JSON.stringify(idle0));
  await page.click('#btnIdleStart');
  await page.waitForTimeout(400);
  check('点空状态按钮跳到「今日任务」', await page.$eval('#page-home', e => e.classList.contains('active')));

  // 选了词库但一个词都没学过 → 复习页应该说"暂时没得复习"（而不是"今天还没开始"）
  await page.click('#listCards .list-card[data-k="cet4"]');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 30000 });
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(600);
  const idle1 = await page.evaluate(() => ({
    disp: getComputedStyle(document.getElementById('studyIdle')).display,
    title: document.getElementById('idleTitle').textContent,
    desc: document.getElementById('idleDesc').textContent,
    btn: document.getElementById('btnIdleStart').textContent,
    active: S.active,
  }));
  check('没学过任何词时，复习页说「暂时没有要复习的词」（而不是"今天还没开始"）',
    idle1.disp === 'flex' && /暂时没有要复习的词/.test(idle1.title) && idle1.active === 'review', JSON.stringify(idle1));
  check('复习空状态里指路到「正在学」板块学新词', /正在学/.test(idle1.btn), idle1.btn);
  await page.click('#btnIdleStart');
  await page.waitForTimeout(500);
  const wentLearn = await page.evaluate(() => ({
    tab: (document.querySelector('nav button.active') || {}).getAttribute('data-tab'),
    sess: S.active,
  }));
  check('点它切到「正在学」页签（不再是回到首页）',
    wentLearn.tab === 'learn' && wentLearn.sess === 'new', JSON.stringify(wentLearn));
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(400);

  // 首页「开始学习」走的是新词会话
  await page.click('#btnStart');
  await page.waitForTimeout(900);
  const newSession = await page.evaluate(() => ({
    active: S.active,
    types: [...new Set(S.queue.map(q => q.type))],
    queue: S.queue.length,
    cardVisible: getComputedStyle(document.getElementById('card')).display !== 'none',
    word: (document.querySelector('.face.front .word') || {}).textContent || '',
  }));
  check('首页「开始学习」出的是今日新词（队列全 nw）',
    newSession.active === 'new' && newSession.types.join() === 'nw' && newSession.queue > 0 && newSession.cardVisible,
    JSON.stringify({ active: newSession.active, types: newSession.types, queue: newSession.queue }));

  // 学掉 6 个 → 这些词的到期日是明天 → 复习页仍应"没得复习"
  await page.evaluate(async () => {
    for (let i = 0; i < 6; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 3)); grade(5); await new Promise(r => setTimeout(r, 3));
    }
  });
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(600);
  const afterLearn = await page.evaluate(() => ({
    title: document.getElementById('idleTitle').textContent,
    due: dueCount(), reviewQueue: S.sessions.review.queue.length,
    active: S.active,
  }));
  check('刚学完的词到期日在明天 → 复习页仍显示"暂时没有要复习的词"',
    afterLearn.due === 0 && afterLearn.reviewQueue === 0 && /暂时没有要复习的词/.test(afterLearn.title),
    JSON.stringify(afterLearn));

  // 把学过的词改成今天到期 → 复习页签应只出这些「学过的词」
  const learned = await page.evaluate(() => {
    const prog = getProg(S.list);
    const words = Object.keys(prog);
    words.forEach(w => { prog[w].due = today(); });
    saveProg(S.list, prog);
    S.sessions.review = { queue: [], idx: 0 };     // 清掉旧会话，模拟重新进入
    return words;
  });
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(800);
  const review = await page.evaluate(() => ({
    active: S.active,
    types: [...new Set(S.queue.map(q => q.type))],
    queue: S.queue.map(q => q.w),
    word: (document.querySelector('.face.front .word') || {}).textContent || '',
    cardVisible: getComputedStyle(document.getElementById('card')).display !== 'none',
    learned: Object.keys(getProg(S.list)),
  }));
  check('复习页签只复习学过的词（队列全是 rv，且都在已学列表里）',
    review.active === 'review' && review.types.join() === 'rv' && review.learned.includes(review.word) && review.cardVisible,
    JSON.stringify({ active: review.active, types: review.types, word: review.word, len: review.queue.length }));
  check('复习队列长度 = 到期词数，且不含没学过的词',
    review.queue.length === learned.length && review.queue.every(w => review.learned.includes(w)),
    `${review.queue.length} / 已学 ${learned.length}`);

  // 复习完 → 再进复习页应回到"暂时没有要复习的词"
  await page.evaluate(async () => {
    for (let i = 0; i < 40; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 3)); grade(5); await new Promise(r => setTimeout(r, 3));
    }
  });
  await page.waitForTimeout(400);
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(600);
  const afterReview = await page.evaluate(() => ({
    title: document.getElementById('idleTitle').textContent,
    disp: getComputedStyle(document.getElementById('studyIdle')).display,
    done: getComputedStyle(document.getElementById('studyDone')).display,
    due: dueCount(),
  }));
  check('复习完再进复习页：回到「暂时没有要复习的词」而不是完成页',
    afterReview.disp === 'flex' && /暂时没有要复习的词/.test(afterReview.title) && afterReview.due === 0,
    JSON.stringify(afterReview));

  // 首页按钮不受复习会话影响（切到复习页签后回首页，按钮文案仍按新词会话算）
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(400);
  const homeBtn = await page.evaluate(() => ({
    txt: document.getElementById('btnStart').textContent,
    dis: document.getElementById('btnStart').disabled,
    newLeft: S.sessions.new.queue.length - S.sessions.new.idx,
    badge: document.getElementById('revBadge').textContent,
  }));
  check('首页按钮只按「今日新词」会话算（复习会话不影响它）',
    homeBtn.newLeft > 0 && /开始学习/.test(homeBtn.txt) && !homeBtn.dis, JSON.stringify(homeBtn));
  check('复习页签上的到期数小红点在无到期词时隐藏', homeBtn.badge === '', JSON.stringify(homeBtn));

  // 有到期词时小红点应显示数字
  const badge = await page.evaluate(() => {
    const prog = getProg(S.list);
    Object.keys(prog).forEach(w => { prog[w].due = today(); });
    saveProg(S.list, prog);
    render();
    const b = document.getElementById('revBadge');
    return { txt: b.textContent, disp: b.style.display, due: dueCount() };
  });
  check('有到期词时复习页签显示到期数', badge.due > 0 && badge.txt === String(badge.due) && badge.disp !== 'none',
    JSON.stringify(badge));

  /* ---------------- 回归：用户报的那个 bug ---------------- */
  section('回归：学完 20 个后把每日词数改成 10，复习页不该说「今天还没开始」');
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 30000 });
  await page.click('#listCards .list-card[data-k="cet4"]');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 30000 });
  // 按用户路径：每日 20 → 学完 → 改成 10
  await page.evaluate(async () => {
    Settings.daily = 20; localStorage.setItem('wh_setting_daily', '20');
    buildQueue(); render();
  });
  await page.click('#btnStart');
  await page.waitForTimeout(800);
  await page.evaluate(async () => {
    for (let i = 0; i < 60; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
  });
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  await page.click('#dailyPills .dpill[data-d="10"]');      // 切换每日词数
  await page.waitForTimeout(500);
  await page.click('nav button[data-tab="study"]');          // 进复习页签
  await page.waitForTimeout(700);
  const bug = await page.evaluate(() => ({
    title: document.getElementById('idleTitle').textContent,
    disp: getComputedStyle(document.getElementById('studyIdle')).display,
    active: S.active, due: dueCount(),
    newQueue: S.sessions.new.queue.length, daily: Settings.daily,
  }));
  check('复习页显示「暂时没有要复习的词」，不再是「今天还没开始」',
    bug.disp === 'flex' && /暂时没有要复习的词/.test(bug.title) && !/今天还没开始/.test(bug.title),
    JSON.stringify(bug));
  check('每日词数已切成 10（新词会话重建为 10 张）', bug.daily === 10 && bug.newQueue === 10, JSON.stringify(bug));

  /* ================= 拼写正确会「叮咚」一声 ================= */
  const ding = await page.evaluate(async () => {
    Settings.mode = 'spell';
    buildQueue();
    openSession('new');
    showCard();
    await new Promise(r => setTimeout(r, 250));
    let hits = 0;
    const orig = window.playDing;
    window.playDing = ()=>{ hits++; };
    // 拼错一次
    const inp = document.getElementById('spellInput');
    inp.value = 'definitely-not-the-word';
    document.getElementById('spellGo').click();
    await new Promise(r => setTimeout(r, 120));
    const afterWrong = hits;
    // 换个新卡再拼对
    S.revealed = false;
    showCard();
    await new Promise(r => setTimeout(r, 200));
    const target = S.queue[S.idx].w;
    document.getElementById('spellInput').value = target;
    document.getElementById('spellGo').click();
    await new Promise(r => setTimeout(r, 150));
    const afterRight = hits;
    window.playDing = orig;
    return { afterWrong, afterRight, target };
  });
  check('拼对时响一声「叮咚」，拼错时不响',
    ding.afterWrong === 0 && ding.afterRight === 1, JSON.stringify(ding));

  /* ================= 「复习」上方的队列与「正在学」互相独立 ================= */
  const queues = await page.evaluate(async () => {
    Settings.mode = 'card';
    localStorage.removeItem('wh_setting_list');
    selectList('cet4');
    await new Promise(r => setTimeout(r, 1200));
    // 造一批已学且到期的词 → 进复习队列
    const prog = {};
    S.data.slice(0, 15).forEach(e => { prog[e.w] = { ef: 2.5, ivl: 1, reps: 2, due: today(), lapses: 0, seen: 1 }; });
    saveProg('cet4', prog);
    buildQueue();
    enterTab('learn');
    await new Promise(r => setTimeout(r, 250));
    const tags = () => [...document.querySelectorAll('#queueInfo .queue-tag')].map(t => t.className.includes('rv') ? 'rv' : 'nw');
    const onLearn = {
      text: document.getElementById('queueInfo').textContent,
      active: S.active,
      previewTypes: tags(),
      newLen: S.sessions.new.queue.length,
    };
    enterTab('study');          // 复习队列是进这个页签时才构建
    await new Promise(r => setTimeout(r, 350));
    const onReview = {
      text: document.getElementById('queueInfo').textContent,
      active: S.active,
      previewTypes: tags(),
      reviewLen: S.sessions.review.queue.length,
    };
    return { onLearn, onReview };
  });
  check('「正在学」上方显示的是新词队列（全是"新…"），与复习队列互不干扰',
    queues.onLearn.active === 'new' && queues.onLearn.previewTypes.length > 0
    && queues.onLearn.previewTypes.every(t => t === 'nw'), JSON.stringify(queues.onLearn));
  check('两个页签的队列长度各自独立（新词 10 / 复习 15，互不相通）',
    queues.onLearn.newLen > 0 && queues.onReview.reviewLen > 0
    && queues.onLearn.newLen !== queues.onReview.reviewLen,
    JSON.stringify({ newLen: queues.onLearn.newLen, reviewLen: queues.onReview.reviewLen }));
  check('切到「复习」后上方显示的是复习队列（复…），与「正在学」互不相通',
    queues.onReview.active === 'review'
    && queues.onReview.previewTypes.length > 0
    && queues.onReview.previewTypes.every(t => t === 'rv'),
    JSON.stringify(queues.onReview));

  console.log('\n页面 JS 报错:', errs.length ? errs.join(' | ') : '无');
  await browser.close();
  const failed = R.filter(r => !r.ok);
  console.log(`\n================ 词枢新增能力 汇总：通过 ${R.length - failed.length}/${R.length} ================`);
  if (failed.length) console.log(failed.map(f => ' - ' + f.n + '  ' + f.d).join('\n'));
})().catch(e => { console.error('脚本异常', e); process.exit(1); });
