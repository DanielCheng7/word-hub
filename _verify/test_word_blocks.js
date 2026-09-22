/* 「正在学」与「复习」板块分离 + 发音链路 的专项验证
 *
 * 板块：点「开始学习」必须落在「正在学」页签（而不是标着「复习」的那页）；
 *       两个板块在首页各自成块；完成页按会话区分；「再学一点」只属于「正在学」。
 * 发音：当 WebView2 拿不到任何英文语音时，必须把词交给桌面壳（系统 SAPI）念，
 *       绝不能退回"随便一个语音"（那会变成中文语音念英文）。
 */
const { chromium } = require('playwright');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const URLX = 'file:///' + path.join(ROOT, 'apps/word-hub/index.html').replace(/\\/g, '/') + '?desktop=1';

const RES = [];
function check(name, ok, detail = '') {
  RES.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const page = await (await browser.newContext({ viewport: { width: 1345, height: 874 } })).newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message).slice(0, 120)));

  await page.addInitScript(() => {
    window.__calls = [];
    const rec = name => (...a) => { window.__calls.push({ name, args: a }); return true; };
    const api = {};
    ['close', 'minimize', 'zoom', 'begin_resize', 'update_resize', 'end_resize',
     'begin_drag', 'drag_move', 'end_drag', 'speak', 'prefetch'].forEach(k => { api[k] = rec(k); });
    window.pywebview = { api };
  });

  await page.goto(URLX, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === 6, null, { timeout: 40000 });
  await page.waitForTimeout(600);

  /* ---------------- 一、页签 ---------------- */
  const nav = await page.evaluate(() => {
    const bs = [...document.querySelectorAll('nav button')];
    return { tabs: bs.map(b => b.dataset.tab), labels: bs.map(b => b.textContent.replace(/\d+$/, '').trim()) };
  });
  check('导航里「正在学」和「复习」是两个并列页签',
    nav.tabs.includes('learn') && nav.tabs.includes('study') && nav.tabs.indexOf('learn') < nav.tabs.indexOf('study'),
    nav.tabs.join(','));
  check('页签文案确实是「正在学」/「复习」',
    nav.labels.includes('正在学') && nav.labels.includes('复习'), nav.labels.join(' | '));

  /* ---------------- 二、首页两块 ---------------- */
  const hero = await page.evaluate(() => {
    const cols = [...document.querySelectorAll('.hero-cols .hero-col')];
    return {
      n: cols.length,
      labs: cols.map(c => (c.querySelector('.hero-lab') || {}).textContent),
      left: (cols[0].querySelector('.hero-lab') || {}).textContent,
      right: (cols[1].querySelector('.hero-lab') || {}).textContent,
      hasStart: !!cols[0].querySelector('#btnStart'),
      hasGoRev: !!cols[1].querySelector('#btnGoReview'),
      goRevDisabled: cols[1].querySelector('#btnGoReview').disabled,
      tNewInLeft: !!cols[0].querySelector('#tNew'),
      tRevInRight: !!cols[1].querySelector('#tRev'),
      sep: getComputedStyle(cols[1]).borderLeftWidth,
    };
  });
  check('首页拆成两块：左「正在学」（含开始学习）、右「复习」（含去复习）',
    hero.n === 2 && hero.left === '正在学' && hero.right === '复习'
    && hero.hasStart && hero.hasGoRev && hero.tNewInLeft && hero.tRevInRight,
    JSON.stringify({ n: hero.n, left: hero.left, right: hero.right }));
  check('两块之间有分隔线，且没有到期词时「去复习」是禁用的',
    hero.sep === '1px' && hero.goRevDisabled === true, JSON.stringify({ sep: hero.sep, dis: hero.goRevDisabled }));

  /* ---------------- 三、点「开始学习」必须落在「正在学」页签 ---------------- */
  await page.click('#listCards .list-card:nth-child(1)');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 30000 });
  await page.click('#btnStart');
  await page.waitForTimeout(700);
  const afterStart = await page.evaluate(() => ({
    activeTab: (document.querySelector('nav button.active') || {}).datasetTab,
    activeTabAttr: (document.querySelector('nav button.active') || {}).getAttribute('data-tab'),
    pageVisible: getComputedStyle(document.getElementById('page-study')).display !== 'none',
    cardVisible: getComputedStyle(document.getElementById('card')).display !== 'none',
    sess: S.active,
    types: [...new Set(S.queue.map(q => q.type))],
  }));
  check('点「开始学习」后落在「正在学」页签（不再是「复习」）',
    afterStart.activeTabAttr === 'learn' && afterStart.pageVisible && afterStart.cardVisible,
    JSON.stringify(afterStart));
  check('此时是今日新词会话（队列全是 nw）',
    afterStart.sess === 'new' && afterStart.types.join() === 'nw', JSON.stringify({ sess: afterStart.sess, types: afterStart.types }));

  /* ---------------- 四、点「复习」页签 → 到期复习会话 ---------------- */
  await page.evaluate(() => {
    // 造"学过 20 个、且今天到期"的状态（此时还没评过分，prog 是空的，必须自己种进去）
    const prog = {};
    S.data.slice(0, 20).forEach(e => {
      prog[e.w] = { ef: 2.5, ivl: 1, reps: 2, due: today(), lapses: 0, seen: 1 };
    });
    saveProg(S.list, prog);
    S.sessions.review = { queue: [], idx: 0 };
  });
  await page.click('nav button[data-tab="study"]');
  await page.waitForTimeout(700);
  const rev = await page.evaluate(() => ({
    activeTabAttr: (document.querySelector('nav button.active') || {}).getAttribute('data-tab'),
    sess: S.active,
    types: [...new Set(S.queue.map(q => q.type))],
    cardVisible: getComputedStyle(document.getElementById('card')).display !== 'none',
  }));
  check('点「复习」页签进入到期复习会话（队列全是 rv）',
    rev.activeTabAttr === 'study' && rev.sess === 'review' && rev.types.join() === 'rv' && rev.cardVisible,
    JSON.stringify(rev));

  /* ---------------- 五、完成页按会话区分 ---------------- */
  await page.evaluate(async () => {
    for (let i = 0; i < 60; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
  });
  await page.waitForTimeout(500);
  const doneRev = await page.evaluate(() => ({
    shown: getComputedStyle(document.getElementById('studyDone')).display !== 'none',
    title: document.getElementById('doneTitle').textContent,
    detail: document.getElementById('doneDetail').textContent,
    moreShown: getComputedStyle(document.getElementById('btnDoneMore')).display !== 'none',
    homeTxt: document.getElementById('btnDoneHome').textContent,
  }));
  check('复习会话的完成页：标题说「这一轮复习完成！」，且不给「再学一点」',
    doneRev.shown && /这一轮复习完成/.test(doneRev.title) && doneRev.moreShown === false,
    JSON.stringify(doneRev));
  check('复习完成页只报"复习 N 次"，不混进"新词"',
    /复习 \d+ 次/.test(doneRev.detail) && !/新词/.test(doneRev.detail), doneRev.detail);
  check('复习完成页的次要按钮指向「正在学」', /正在学/.test(doneRev.homeTxt), doneRev.homeTxt);

  // 再回到「正在学」走完新词 → 完成页应说"今日新词学完了"
  await page.click('nav button[data-tab="learn"]');
  await page.waitForTimeout(500);
  await page.evaluate(async () => {
    for (let i = 0; i < 60; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
  });
  await page.waitForTimeout(500);
  const doneNew = await page.evaluate(() => ({
    title: document.getElementById('doneTitle').textContent,
    detail: document.getElementById('doneDetail').textContent,
    moreShown: getComputedStyle(document.getElementById('btnDoneMore')).display !== 'none',
    homeTxt: document.getElementById('btnDoneHome').textContent,
  }));
  check('新词会话的完成页：标题说「今日新词学完了！」并给「再学一点」',
    /今日新词学完了/.test(doneNew.title) && doneNew.moreShown === true, JSON.stringify(doneNew));
  check('新词完成页只报"新词 N 个"，不混进"复习"',
    /新词 \d+ 个/.test(doneNew.detail) && !/复习/.test(doneNew.detail), doneNew.detail);

  /* ---------------- 六、复习板块按钮随到期数变化 ---------------- */
  const revBtn = await page.evaluate(() => {
    const prog = getProg(S.list);
    Object.keys(prog).forEach(w => { prog[w].due = today(); });
    saveProg(S.list, prog);
    render();
    const b = document.getElementById('btnGoReview');
    return { txt: b.textContent, dis: b.disabled, tRev: document.getElementById('tRev').textContent };
  });
  check('有到期词时「复习」板块显示数量并可点击',
    !revBtn.dis && /去复习\s*\d+\s*个/.test(revBtn.txt) && parseInt(revBtn.tRev) > 0, JSON.stringify(revBtn));

  const noRev = await page.evaluate(() => {
    const prog = getProg(S.list);
    Object.keys(prog).forEach(w => { prog[w].due = today() + 30; });
    saveProg(S.list, prog);
    render();
    const b = document.getElementById('btnGoReview');
    return { txt: b.textContent, dis: b.disabled, desc: document.getElementById('revDesc').textContent };
  });
  check('没有到期词时「复习」板块禁用并说明', noRev.dis === true && /暂时没有到期/.test(noRev.desc), JSON.stringify(noRev));

  /* ---------------- 七、发音：没有英文语音时交给桌面壳 ---------------- */
  const voice = await page.evaluate(() => {
    const all = speechSynthesis.getVoices();
    const en = all.filter(v => (v.lang || '').toLowerCase().startsWith('en'));
    return { total: all.length, en: en.length, picked: (() => { const v = pickVoice(); return v ? v.name : null; })() };
  });
  check('这台机器 WebView2 里确实没有英文语音（正是发音变差的根因）',
    voice.en === 0 && voice.picked === null, JSON.stringify(voice));

  await page.evaluate(() => { window.__calls.length = 0; });
  await page.evaluate(() => speak('vocabulary'));
  await page.waitForTimeout(200);
  const spoken = await page.evaluate(() => window.__calls.filter(c => c.name === 'speak').map(c => c.args));
  check('没有英文语音时，朗读交给桌面壳（系统 SAPI）而不是硬用中文语音',
    spoken.length === 1 && spoken[0][0] === 'vocabulary', JSON.stringify(spoken));

  /* ---------------- 七点一、在线真人发音的 URL 与口音 ---------------- */
  const urls = await page.evaluate(() => ({
    us: onlineAudioUrl('vocabulary', 'en-US'),
    gb: onlineAudioUrl('vocabulary', 'en-GB'),
    enc: onlineAudioUrl('ice cream', 'en-US'),
  }));
  check('在线真人发音：美音 type=2、英音 type=1，词做过 URL 编码',
    /type=2/.test(urls.us) && /type=1/.test(urls.gb) && /dict\.youdao\.com/.test(urls.us)
    && /audio=ice%20cream/.test(urls.enc),
    JSON.stringify(urls));

  /* ---------------- 七点二、桌面壳拿不到音频时退回浏览器侧 ---------------- */
  // 注意：给 speechSynthesis「实例」赋 speak 会被静默忽略（原生对象），
  // 所以这里改成替换全局函数 speakInBrowser 来观测（function 声明会挂到 window 上）
  const fb = await page.evaluate(async () => {
    const spy = [];
    const origFn = window.speakInBrowser;
    const origApi = window.pywebview.api.speak;
    window.speakInBrowser = w => spy.push(w);
    window.pywebview.api.speak = () => Promise.resolve(false);   // 桌面壳说"我拿不到音频"
    speak('vocabulary');
    await new Promise(r => setTimeout(r, 200));
    window.speakInBrowser = origFn;
    window.pywebview.api.speak = origApi;
    return { spy };
  });
  check('桌面壳拿不到音频时（返回 false）自动退回浏览器侧',
    fb.spy.length === 1 && fb.spy[0] === 'vocabulary', JSON.stringify(fb));

  /* ---------------- 七点二之二、桌面壳成功时就不再走浏览器侧 ---------------- */
  const fb2 = await page.evaluate(async () => {
    const spy = [];
    const origFn = window.speakInBrowser;
    const origApi = window.pywebview.api.speak;
    window.speakInBrowser = w => spy.push(w);
    window.pywebview.api.speak = () => Promise.resolve(true);    // 桌面壳说"我放出来了"
    speak('vocabulary');
    await new Promise(r => setTimeout(r, 200));
    window.speakInBrowser = origFn;
    window.pywebview.api.speak = origApi;
    return { spy };
  });
  check('桌面壳成功播放时不再重复走浏览器侧（不会念两遍）',
    fb2.spy.length === 0, JSON.stringify(fb2));

  /* ---------------- 七点三、浏览器里没有英文语音时放词典音频 ---------------- */
  const online = await page.evaluate(async () => {
    const played = [];
    const origPlay = window.HTMLMediaElement.prototype.play;
    window.HTMLMediaElement.prototype.play = function () { played.push(String(this.src)); return Promise.resolve(); };
    const origAudio = window.Audio;
    window.Audio = function () { return document.createElement('audio'); };
    const keepApi = window.pywebview; window.pywebview = undefined;   // 模拟纯浏览器
    const old = document.getElementById('onlineAudio');
    if (old) old.remove();
    try { speakInBrowser('vocabulary'); } catch (e) { return { err: String(e) }; }
    await new Promise(r => setTimeout(r, 150));
    window.pywebview = keepApi;
    window.HTMLMediaElement.prototype.play = origPlay;
    window.Audio = origAudio;
    return { played, hasEl: !!document.getElementById('onlineAudio') };
  });
  check('浏览器里没有英文语音时，改为播放词典真人音频',
    online.played.length >= 1 && /dict\.youdao\.com/.test(online.played[0]) && /type=2/.test(online.played[0]),
    JSON.stringify(online));

  /* ---------------- 七点四、卡片预取 ---------------- */
  const pf = await page.evaluate(async () => {
    window.__calls.length = 0;
    prefetchAudio('hello');
    await new Promise(r => setTimeout(r, 60));
    return { calls: window.__calls.filter(c => c.name === 'prefetch').map(c => c.args), accent: Settings.accent };
  });
  check('翻到新卡时会提前预取该词音频（点 🔊 不用等下载）',
    pf.calls.length === 1 && pf.calls[0][0] === 'hello' && pf.calls[0][1] === pf.accent,
    JSON.stringify(pf));

  /* ---------------- 七点五、卡片背面也要有发音按钮 ---------------- */
  await page.evaluate(() => {
    Settings.daily = 20;
    const prog = {};
    S.data.slice(0, 20).forEach(e => {
      prog[e.w] = { ef: 2.5, ivl: 1, reps: 1, due: today() + 30, lapses: 0, seen: 1 };
    });
    saveProg(S.list, prog);
    buildQueue();
    openSession('new');
    showCard();
    reveal();
  });
  await page.waitForTimeout(500);
  const backSpk = await page.evaluate(() => {
    const btn = document.querySelector('#faceBack .speaker-mini');
    if (!btn) return { exists: false };
    const r = btn.getBoundingClientRect();
    window.__calls.length = 0;
    const before = S.revealed;
    btn.click();
    return {
      exists: true, w: Math.round(r.width), h: Math.round(r.height),
      calls: window.__calls.filter(c => c.name === 'speak').map(c => c.args),
      stillFlipped: S.revealed === before,
    };
  });
  check('卡片背面也有 🔊 发音按钮，点了会读当前词（且不会把卡片翻回去）',
    backSpk.exists && backSpk.calls.length === 1 && backSpk.stillFlipped === true,
    JSON.stringify(backSpk));
  check('背面发音按钮的可点区域够大（≥32px）',
    backSpk.exists && backSpk.w >= 32 && backSpk.h >= 32, `${backSpk.w}×${backSpk.h}`);

  /* ---------------- 八、纯浏览器下：有英文语音走语音合成，没有才放词典音频 ---------------- */
  const browserVoiced = await page.evaluate(async () => {
    const origGet = speechSynthesis.getVoices;
    speechSynthesis.getVoices = () => [{ name: 'Test English', lang: 'en-US' }];
    const keepApi = window.pywebview; window.pywebview = undefined;   // 模拟纯浏览器
    const old = document.getElementById('onlineAudio');
    if (old) old.remove();
    let created = false;
    const origPlay = window.HTMLMediaElement.prototype.play;
    window.HTMLMediaElement.prototype.play = function () { created = true; return Promise.resolve(); };
    const origAudio = window.Audio;
    window.Audio = function () { return document.createElement('audio'); };
    try { speakInBrowser('hello'); } catch (e) {}
    await new Promise(r => setTimeout(r, 150));
    window.pywebview = keepApi;
    window.HTMLMediaElement.prototype.play = origPlay;
    window.Audio = origAudio;
    speechSynthesis.getVoices = origGet;
    return { created, picked: (() => { const S = speechSynthesis.getVoices(); return S.length ? S[0].name : null; })() };
  });
  check('纯浏览器且系统有英文语音时走语音合成（不会去放词典音频）',
    browserVoiced.created === false, JSON.stringify(browserVoiced));

  /* ---------------- 八点一、桌面版一律先交给桌面壳（在线真人发音更自然）---------------- */
  const alwaysDesk = await page.evaluate(async () => {
    const orig = window.pywebview.api.speak;
    const seen = [];
    window.pywebview.api.speak = (w, a) => { seen.push([w, a]); return Promise.resolve(true); };
    const origGet = speechSynthesis.getVoices;
    speechSynthesis.getVoices = () => [{ name: 'Test English', lang: 'en-US' }];  // 就算浏览器有语音
    speak('hello');
    await new Promise(r => setTimeout(r, 150));
    window.pywebview.api.speak = orig;
    speechSynthesis.getVoices = origGet;
    return { seen };
  });
  check('桌面版即使浏览器有英文语音，也先交给桌面壳（在线真人发音最自然）',
    alwaysDesk.seen.length === 1 && alwaysDesk.seen[0][0] === 'hello', JSON.stringify(alwaysDesk.seen));

  console.log('\n页面 JS 报错:', errs.length ? errs.join(' | ') : '无');
  console.log(`\n================ 板块分离与发音 汇总：通过 ${RES.filter(Boolean).length}/${RES.length} ================`);
  await browser.close();
  process.exit(RES.every(Boolean) ? 0 : 1);
})().catch(e => { console.error('测试异常：', e); process.exit(1); });
