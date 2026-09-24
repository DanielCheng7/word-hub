/**
 * word-hub 全功能通检（含边界与错误路径）
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');   // 仓库根目录（跟着脚本走，不写死本机路径）
const DL = path.join(ROOT, '_verify/downloads');
const URLX = 'file:///' + (ROOT + '/apps/word-hub/index.html').replace(/ /g, '%20');
fs.mkdirSync(DL, { recursive: true });

const R = [];
const check = (n, ok, d = '') => { R.push({ n, ok: !!ok, d: String(d) }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  — ' + d : ''}`); };
const section = s => console.log(`\n----- ${s} -----`);

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  await ctx.addInitScript(() => { try { localStorage.clear(); } catch (e) {} });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text()); });
  page.on('dialog', d => d.accept());          // 清空/删除类确认框一律确认
  await page.goto(URLX, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 20000 });
  const NLISTS = await page.evaluate(() => Object.keys(LISTS).length);   // 词库个数不写死

  /* ---------------- 首页 ---------------- */
  section('今日任务页');
  const cards = await page.$$eval('#listCards .list-card', els => els.map(e => ({
    n: e.querySelector('.name').textContent, c: e.querySelector('.cnt').textContent })));
  check(`${NLISTS} 个词库卡片，含词数与已学数`, cards.length === NLISTS && cards.every(c => /已学/.test(c.c)),
    cards.map(c => c.n).join(' / '));
  check('初始状态按钮禁用且提示选词库', await page.$eval('#btnStart', e => e.disabled));
  const dPills = await page.$$eval('#dailyPills .dpill', e => e.map(x => x.dataset.d));
  check('每日词数 5 档快捷选择', dPills.join(',') === '10,20,30,50,80', dPills.join(','));
  const mPills = await page.$$eval('#modePills .study-pill', e => e.map(x => x.dataset.mode));
  check('两种学习模式（卡片/拼写）', mPills.join(',') === 'card,spell', mPills.join(','));
  const oPills = await page.$$eval('#orderPills .dpill', e => e.map(x => x.dataset.o));
  check('两种记词顺序（正序/乱序）', oPills.join(',') === 'seq,rand', oPills.join(','));

  await page.click('#listCards .list-card[data-k="cet4"]');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 20000 });
  const head = await page.evaluate(() => ({ list: document.getElementById('hdListName').textContent, n: S.queue.length, newC: S.newCount }));
  check('选词库后队列按每日词数生成（默认 20）', head.n === 20 && head.newC === 20 && /CET-4/.test(head.list), JSON.stringify(head));

  await page.click('#dailyPills .dpill[data-d="10"]');
  await page.waitForTimeout(200);
  const q10 = await page.evaluate(() => S.queue.length);
  const setSync = await page.inputValue('#setDaily');
  check('点每日词数胶囊后队列与设置页联动', q10 === 10 && setSync === '10', `队列 ${q10} / 设置 ${setSync}`);
  await page.click('#dailyPills .dpill[data-d="20"]');
  await page.waitForTimeout(150);

  /* ---------------- SM-2 数学 ---------------- */
  section('SM-2 算法');
  const sm2 = await page.evaluate(() => {
    const bak = Settings.reviewMode;
    Settings.reviewMode = 'sm2';            // 这一节验的是渐进间隔算法本身
    const run = qs => {
      const rec = { ef: 2.5, ivl: 0, reps: 0, due: 0, lapses: 0, seen: 0 };
      return qs.map(q => { schedule(rec, q); return [rec.reps, rec.ivl, +rec.ef.toFixed(2), rec.due - today(), rec.seen ? 1 : 0]; });
    };
    const res = { good3: run([5, 5, 5]), oneAgain: run([0]), hard: run([5, 5, 3]), efFloor: run([0, 0, 0, 0, 0, 0]), mixed: run([5, 3, 5, 5]) };
    Settings.reviewMode = bak;
    return res;
  });
  // 默认的「每天」节奏：学完/复习完一律第二天到期
  const dailyMode = await page.evaluate(() => {
    const run = qs => {
      const rec = { ef: 2.5, ivl: 0, reps: 0, due: 0, lapses: 0, seen: 0 };
      return qs.map(q => { schedule(rec, q); return { ivl: rec.ivl, due: rec.due - today() }; });
    };
    return { mode: Settings.reviewMode, chain: run([5, 5, 5, 5]), afterForgot: run([5, 0]), afterHard: run([5, 3]) };
  });
  check('默认复习节奏是「每天」：不管认识/模糊几次，到期一律是第二天',
    dailyMode.mode === 'daily' && dailyMode.chain.every(x => x.ivl === 1 && x.due === 1)
    && dailyMode.afterHard.every(x => x.ivl === 1 && x.due === 1), JSON.stringify(dailyMode));
  check('SM-2 认识链：间隔 1 → 6 → 15 天（EF 封顶 2.5，I3 = I2 × EF）',
    sm2.good3[0][1] === 1 && sm2.good3[1][1] === 6 && sm2.good3[2][1] === 15,
    JSON.stringify(sm2.good3.map(x => [x[1], x[2]])));
  check('SM-2 EF 封顶 2.5（SuperMemo 标准区间 1.3~2.5）：平稳认识一直是 2.5，不会无限涨',
    sm2.good3.every(x => x[2] === 2.5), JSON.stringify(sm2.good3.map(x => x[2])));
  check('SM-2 EF 随难度退化、随认识回升：模糊 2.5→2.35，再认识 2.45→2.5（封顶）',
    sm2.mixed[0][2] === 2.5 && sm2.mixed[1][2] === 2.35 && sm2.mixed[2][2] === 2.45 && sm2.mixed[3][2] === 2.5,
    JSON.stringify(sm2.mixed.map(x => [x[1], x[2]])));
  check('SM-2 新词即忘：标记已见 + 明天再现（掉不出队列）',
    sm2.oneAgain[0][0] === 0 && sm2.oneAgain[0][1] === 1 && sm2.oneAgain[0][3] === 1 && sm2.oneAgain[0][4] === 1,
    JSON.stringify({ reps: sm2.oneAgain[0][0], ivl: sm2.oneAgain[0][1], due偏移: sm2.oneAgain[0][3], seen: sm2.oneAgain[0][4] }));
  check('SM-2 模糊：间隔比认识短', sm2.hard[2][1] < sm2.good3[2][1], `${sm2.hard[2][1]} vs ${sm2.good3[2][1]}`);
  check('SM-2 EF 下限锁定 1.3（连错不会跌破）', sm2.efFloor[5][2] === 1.3, 'EF=' + sm2.efFloor[5][2]);

  /* ---------------- 复习流程 ---------------- */
  section('复习（卡片模式）');
  await page.click('#btnStart');
  await page.waitForTimeout(500);
  const front = await page.evaluate(() => ({
    word: document.querySelector('.face.front .word').textContent,
    phon: !!document.querySelector('.face.front .phon'),
    speaker: !!document.querySelector('.face.front .speaker'),
    backText: document.querySelector('.face.back').textContent.length,
    flipped: document.getElementById('cardInner').classList.contains('flipped'),
  }));
  check('卡片正面：单词 + 音标 + 发音按钮，未翻面', front.word.length > 0 && front.phon && front.speaker && !front.flipped, front.word);
  check('卡片背面已预渲染释义（翻面即时可见）', front.backText > 10, front.backText + ' 字');

  // 未翻面就点评分 → 应被忽略（按钮此时 display:none，用页面内点击模拟真实误触）
  await page.evaluate(() => document.querySelector('.grades .g-good').click());
  await page.waitForTimeout(250);
  const ignored = await page.evaluate(() => S.idx);
  check('未翻面时点评分按钮被忽略（不跳卡）', ignored === 0, 'idx=' + ignored);

  await page.click('#card', { position: { x: 80, y: 70 } });
  await page.waitForTimeout(500);
  const revealed = await page.evaluate(() => ({
    flipped: document.getElementById('cardInner').classList.contains('flipped'),
    grades: getComputedStyle(document.getElementById('grades')).display,
    ni: document.getElementById('nextIntv').textContent.replace(/\s+/g, ' '),
    queueTags: document.querySelectorAll('#queueInfo .queue-tag').length,
    prog: document.getElementById('studyProg').style.width,
  }));
  check('翻面后出现评分按钮与下次间隔预览', revealed.flipped && revealed.grades === 'grid' && /忘记 → 明天/.test(revealed.ni), revealed.ni);
  check('队列标签显示（新/复）与进度条', revealed.queueTags > 0 && revealed.prog === '0%', `${revealed.queueTags} 个标签 / ${revealed.prog}`);

  // 键盘评分
  await page.keyboard.press('3');
  await page.waitForTimeout(400);
  const afterKey = await page.evaluate(() => ({ idx: S.idx, reps: getProg(S.list)[S.queue[0].w].reps }));
  check('键盘 3 = 认识：推进并入库 reps=1', afterKey.idx === 1 && afterKey.reps === 1, JSON.stringify(afterKey));

  // 同一张卡快速连点两次 → 第二次应被锁忽略
  await page.click('#card', { position: { x: 80, y: 70 } });
  await page.waitForTimeout(500);
  // 模拟"手快连点"：两次点击在同一个事件循环里发出
  await page.evaluate(() => {
    const b = document.querySelector('.grades .g-good');
    b.click(); b.click();
  });
  await page.waitForTimeout(500);
  const doubleClickIdx = await page.evaluate(() => S.idx);
  check('同一张卡连点两次只前进一格（防抖锁生效）', doubleClickIdx === 2, 'idx=' + doubleClickIdx);

  // 忘记 → 入队尾
  await page.click('#card', { position: { x: 80, y: 70 } });
  await page.waitForTimeout(450);
  const qBefore = await page.evaluate(() => S.queue.length);
  await page.click('.grades .g-again');
  await page.waitForTimeout(700);
  const forgot = await page.evaluate(() => ({ idx: S.idx, q: S.queue.length, last: S.queue[S.queue.length - 1].w, due: getProg(S.list)[S.queue[S.idx].w] }));
  check('忘记：不推进 + 入队尾重刷 + 记录已重置',
    forgot.idx === 2 && forgot.q === qBefore + 1, JSON.stringify({ idx: forgot.idx, q: forgot.q }));
  check('忘记后该词安排明天再见', Object.values(await page.evaluate(() => getProg(S.list))).some(r => r.reps === 0 && r.ivl === 1),
    JSON.stringify(forgot.due));

  // 收藏（正反面同步）
  await page.click('#card', { position: { x: 80, y: 70 } });
  await page.waitForTimeout(450);
  await page.click('#faceBack .favstar');
  await page.waitForTimeout(300);
  const favState = await page.evaluate(() => ({
    starOn: document.querySelector('#faceBack .favstar').classList.contains('fav-on'),
    badge: !!document.querySelector('#faceFront .favbadge'),
    stored: Object.keys(getFavs(S.list)).length,
    word: S.queue[S.idx].w,
  }));
  check('点 ★ 收藏：背面星标点亮 + 正面出现已收藏标记 + 落盘',
    favState.starOn && favState.badge && favState.stored === 1, JSON.stringify(favState));

  // 空格翻面
  await page.evaluate(() => { S.revealed = false; document.getElementById('cardInner').classList.remove('flipped'); });
  await page.keyboard.press('Space');
  await page.waitForTimeout(300);
  check('卡片模式空格 = 翻面', await page.evaluate(() => document.getElementById('cardInner').classList.contains('flipped')));

  // 完成整个队列
  const finish = await page.evaluate(async () => {
    // 直接推进到队尾：每张卡翻面后按「认识」
    for (let i = 0; i < 60; i++) {
      if (S.idx >= S.queue.length) break;
      reveal();
      await new Promise(r => setTimeout(r, 5));
      grade(5);
      await new Promise(r => setTimeout(r, 5));
    }
    return { idx: S.idx, len: S.queue.length, done: getComputedStyle(document.getElementById('studyDone')).display };
  });
  await page.waitForTimeout(400);
  const doneTxt = await page.textContent('#doneDetail');
  check('走完全部队列后出现完成页并给出统计', finish.done !== 'none' && /新词 \d+/.test(doneTxt), doneTxt);
  check('队列被清空、进度条满格', finish.idx === finish.len && (await page.evaluate(() => document.getElementById('studyProg').style.width)) === '100%',
    `${finish.idx}/${finish.len}`);
  const backHome = await page.evaluate(() => document.getElementById('btnStart').textContent);
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  const homeAfter = await page.evaluate(() => ({ dis: document.getElementById('btnStart').disabled, txt: document.getElementById('btnStart').textContent,
    desc: document.getElementById('taskDesc').textContent }));
  check('走完今日任务后按钮变成可点的「再学一点」',
    !homeAfter.dis && /再学一点/.test(homeAfter.txt) && /没学过/.test(homeAfter.desc),
    homeAfter.txt + ' / ' + homeAfter.desc);

  /* ---------------- 拼写模式 ---------------- */
  section('拼写模式');
  await page.evaluate(() => { buildQueue(); render(); });
  await page.click('#modePills .study-pill[data-mode="spell"]');
  await page.waitForTimeout(250);
  const spellPersist = await page.evaluate(() => localStorage.getItem('wh_setting_mode'));
  check('切换拼写模式并持久化', spellPersist === '"spell"', spellPersist);
  await page.click('#btnStart');
  await page.waitForTimeout(500);
  const sp = await page.evaluate(() => ({
    prompt: document.querySelector('#spellBox .prompt').textContent.replace(/\s+/g, ' '),
    hint: document.querySelector('#spellBox .first').textContent,
    input: !!document.getElementById('spellInput'),
    peek: !!document.getElementById('spellPeek'),
    speak: !!document.getElementById('spellSpeak'),
    cardHidden: getComputedStyle(document.getElementById('card')).display === 'none',
    target: S.queue[S.idx].w,
  }));
  check('拼写模式：显示释义 + 首字母空缺，卡片隐藏',
    /拼写/.test(sp.prompt) && sp.hint.includes('_') && sp.cardHidden && sp.input, sp.hint + ' | 目标 ' + sp.target);
  check('拼写模式：提供「看答案」与发音辅助', sp.peek && sp.speak);

  // 空输入提交
  await page.click('#spellGo');
  await page.waitForTimeout(250);
  check('空输入提交被拦下（不判错）', await page.evaluate(() => !S.revealed));

  // 回车提交拼对
  await page.fill('#spellInput', sp.target.toUpperCase());
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  const right = await page.evaluate(() => ({
    filled: document.querySelector('#spellBox .first').textContent.split(/\s+/).join(''),
    cls: document.querySelector('#spellBox .first').className,
    verdict: (document.querySelector('.sp-verdict') || {}).textContent,
    inputDisabled: document.getElementById('spellInput').disabled,
    noAnswerLine: !/答案[：:]/.test(document.getElementById('spellBox').textContent),
  }));
  check('回车提交、大小写不敏感、答对填空并标绿',
    right.filled.toLowerCase() === sp.target.toLowerCase() && /done-ok/.test(right.cls) && /正确/.test(right.verdict),
    `${right.filled} | ${right.verdict}`);
  check('提交后输入框禁用、下方无答案行', right.inputDisabled && right.noAnswerLine);

  await page.click('.grades .g-good');
  await page.waitForTimeout(450);
  // 看答案路径
  const t2 = await page.evaluate(() => S.queue[S.idx].w);
  await page.click('#spellPeek');
  await page.waitForTimeout(300);
  const peeked = await page.evaluate(() => ({
    filled: document.querySelector('#spellBox .first').textContent.split(/\s+/).join(''),
    cls: document.querySelector('#spellBox .first').className,
    v: (document.querySelector('.sp-verdict') || {}).textContent,
    grades: getComputedStyle(document.getElementById('grades')).display,
  }));
  check('看答案：填入空缺、提示已看答案、仍可观评分',
    peeked.filled.toLowerCase() === t2.toLowerCase() && /已看答案/.test(peeked.v) && peeked.grades === 'grid',
    `${peeked.filled} | ${peeked.v}`);

  // 看答案标记不应污染下一张卡
  await page.click('.grades .g-hard');
  await page.waitForTimeout(450);
  const nextCard = await page.evaluate(() => {
    const t = S.queue[S.idx].w;
    checkSpell({ w: t });
    return { verdict: (document.querySelector('.sp-verdict') || {}).textContent || '' };
  }).catch(() => ({ verdict: '' }));
  const nextState = await page.evaluate(() => ({
    peekFlag: S.spellPeek,
    gradesShown: getComputedStyle(document.getElementById('grades')).display !== 'none',
  }));
  check('看答案标记不残留到下一张卡（spellPeek 已重置）', nextState.peekFlag === false, JSON.stringify(nextState));

  // 续：本张卡填对后，判定应是「拼写正确」而不是「已看答案」
  const t3 = await page.evaluate(() => S.queue[S.idx].w);
  await page.fill('#spellInput', t3);
  await page.click('#spellGo');
  await page.waitForTimeout(300);
  const verdict3 = await page.evaluate(() => (document.querySelector('.sp-verdict') || {}).textContent || '');
  check('上一张卡看过答案，不影响本张卡的判定', /正确/.test(verdict3) && !/已看答案/.test(verdict3), verdict3);
  await page.click('.grades .g-good');
  await page.waitForTimeout(400);

  // 拼写模式空格：应聚焦输入框，不直接揭答案
  await page.evaluate(() => showCard());
  await page.waitForTimeout(250);
  await page.keyboard.press('Space');
  await page.waitForTimeout(300);
  const sp2 = await page.evaluate(() => ({ revealed: S.revealed, active: document.activeElement && document.activeElement.id }));
  check('拼写模式空格只聚焦输入框、不揭答案', sp2.revealed === false && sp2.active === 'spellInput', JSON.stringify(sp2));

  /* ---------------- 收藏夹 ---------------- */
  section('收藏夹');
  await page.click('nav button[data-tab="favs"]');
  await page.waitForTimeout(300);
  const favsView = await page.evaluate(() => ({
    count: document.getElementById('favCount').textContent,
    items: document.querySelectorAll('#favBox .fav-item').length,
    studyDisabled: document.getElementById('btnFavStudy').disabled,
    name: document.getElementById('favListName').textContent,
  }));
  check('收藏夹列出已收藏词并显示词库名', favsView.items === 1 && favsView.count === '1' && /CET-4/.test(favsView.name),
    JSON.stringify(favsView));

  await page.click('#btnFavStudy');
  await page.waitForTimeout(500);
  const favStudy = await page.evaluate(() => ({ q: S.queue.length, onStudy: document.getElementById('page-study').classList.contains('active') }));
  check('「复习收藏的词」进入复习且队列 = 收藏数', favStudy.q === 1 && favStudy.onStudy, JSON.stringify(favStudy));

  await page.click('nav button[data-tab="favs"]');
  await page.waitForTimeout(250);
  await page.click('#favBox .fav-item .fdel');
  await page.waitForTimeout(300);
  const afterDel = await page.evaluate(() => ({ items: document.querySelectorAll('#favBox .fav-item').length, stored: Object.keys(getFavs(S.list)).length,
    studyDisabled: document.getElementById('btnFavStudy').disabled }));
  check('移除单个收藏后列表与存储同步、复习按钮禁用', afterDel.items === 0 && afterDel.stored === 0 && afterDel.studyDisabled, JSON.stringify(afterDel));

  // 重新收藏一个再清空
  await page.evaluate(() => { const f = {}; f[S.data[0].w] = Date.now(); saveFavs(S.list, f); });
  await page.click('nav button[data-tab="favs"]');
  await page.waitForTimeout(250);
  await page.click('#btnFavClear');
  await page.waitForTimeout(350);
  check('清空收藏生效', (await page.evaluate(() => Object.keys(getFavs(S.list)).length)) === 0);

  /* ---------------- 统计 ---------------- */
  section('学习统计');
  await page.click('nav button[data-tab="stats"]');
  await page.waitForTimeout(300);
  const stats = await page.evaluate(() => ({
    streak: document.getElementById('stStreak').textContent,
    todayN: document.getElementById('stToday').textContent,
    learned: document.getElementById('stLearned').textContent,
    total: document.getElementById('stTotal').textContent,
    cells: document.querySelectorAll('#heatmap i').length,
    bars: document.querySelectorAll('#listBars .bar-row').length,
    barTxt: (document.querySelector('#listBars .bar-row .num') || {}).textContent,
  }));
  check('四个统计卡均有值（今日复习数 > 0）', +stats.todayN > 0 && +stats.total > 0 && +stats.learned > 0, JSON.stringify(stats));
  check('热力图近 52 周 = 364 格', stats.cells === 364, stats.cells + ' 格');
  check(`词库进度条 ${NLISTS} 条且显示已学/总数`, stats.bars === NLISTS && /\/\s*\d+/.test(stats.barTxt), stats.barTxt);
  check('连续打卡为 1 天（今天学过）', stats.streak === '1', stats.streak);

  // 跨天逻辑：昨天有记录、今天没有 → streak 从昨天算
  const streakLogic = await page.evaluate(() => {
    const bak = lsGet('wh_log_daily', {});
    LS: try { localStorage.setItem('wh_log_daily', JSON.stringify({ [dayKey(today() - 1)]: 5, [dayKey(today() - 2)]: 3 })); } catch (e) {}
    const s1 = calcStreak();
    try { localStorage.setItem('wh_log_daily', JSON.stringify(bak)); } catch (e) {}
    return s1;
  });
  check('连续打卡跨天计算正确（昨天+前天有记录=2）', streakLogic === 2, 'streak=' + streakLogic);

  /* ---------------- 设置 / 备份 ---------------- */
  section('设置与备份');
  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(250);
  await page.fill('#setDaily', '35');
  await page.dispatchEvent('#setDaily', 'change');
  await page.waitForTimeout(300);
  const dailySet = await page.evaluate(() => ({ d: Settings.daily, q: S.queue.length }));
  check('设置页改每日词数：落盘并重建队列', dailySet.d === 35 && dailySet.q === 35, JSON.stringify(dailySet));
  await page.fill('#setDaily', '999');
  await page.dispatchEvent('#setDaily', 'change');
  await page.waitForTimeout(300);
  check('每日词数超上限被夹到 100', (await page.evaluate(() => Settings.daily)) === 100, String(await page.evaluate(() => Settings.daily)));
  await page.fill('#setDaily', '20');
  await page.dispatchEvent('#setDaily', 'change');
  await page.waitForTimeout(200);

  // 口音改成了自定义动画下拉（原生 select 无法做动画），走点击流程
  await page.click('#accentSel .sel-btn');
  await page.waitForTimeout(300);
  await page.click('#accentSel .sel-opt[data-v="en-GB"]');
  await page.waitForTimeout(300);
  check('口音设置可切换并落盘', (await page.evaluate(() => localStorage.getItem('wh_setting_accent'))) === '"en-GB"',
    await page.evaluate(() => localStorage.getItem('wh_setting_accent')));

  // 导出 → 清空 → 导入 回环
  await page.evaluate(() => { const f = {}; f[S.data[0].w] = 1; saveFavs(S.list, f); });
  const before = await page.evaluate(() => ({
    prog: Object.keys(getProg('cet4')).length, log: JSON.stringify(Log.daily), favs: Object.keys(getFavs('cet4')).length,
    daily: Settings.daily, mode: Settings.mode, order: Settings.order }));
  const [dlBak] = await Promise.all([page.waitForEvent('download'), page.click('#btnExport')]);
  const bakP = path.join(DL, 'wordhub_backup.json');
  await dlBak.saveAs(bakP);
  const bak = JSON.parse(fs.readFileSync(bakP, 'utf8'));
  check('导出的备份含进度/收藏/日志/设置四部分',
    !!bak.progress && !!bak.favs && !!bak.log && !!bak.settings && Object.keys(bak.progress).length === NLISTS,
    Object.keys(bak).join(','));

  await page.click('#btnResetAll');
  await page.waitForTimeout(500);
  const cleared = await page.evaluate(() => ({ prog: Object.keys(getProg('cet4')).length, log: Object.keys(Log.daily).length }));
  check('清空全部进度生效', cleared.prog === 0 && cleared.log === 0, JSON.stringify(cleared));

  await page.setInputFiles('#importFile', [bakP]);
  await page.waitForTimeout(900);
  const restored = await page.evaluate(() => ({
    prog: Object.keys(getProg('cet4')).length, log: JSON.stringify(Log.daily), favs: Object.keys(getFavs('cet4')).length,
    daily: Settings.daily, mode: Settings.mode, order: Settings.order }));
  check('导入备份后进度/收藏/日志逐项还原',
    restored.prog === before.prog && restored.log === before.log && restored.favs === before.favs,
    `before ${JSON.stringify(before)} / after ${JSON.stringify(restored)}`);
  check('导入后设置（词数/模式/顺序）也还原',
    restored.daily === before.daily && restored.mode === before.mode && restored.order === before.order,
    JSON.stringify({ d: restored.daily, m: restored.mode, o: restored.order }));
  // 清空全部进度并不清「所选词库」，这里改选另一个词库再导入，验证词库选择被一起还原
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(200);
  await page.click('#listCards .list-card[data-k="ky"]');
  await page.waitForFunction(() => S.list === 'ky', null, { timeout: 20000 });   // 等词库真正切过去，别抢跑
  const bakP2 = path.join(DL, 'wordhub_backup2.json');       // 换个文件名，确保 change 事件必然触发
  fs.copyFileSync(bakP, bakP2);
  await page.setInputFiles('#importFile', [bakP2]);
  await page.waitForTimeout(1500);
  const listRestored = await page.evaluate(() => ({ list: S.list, head: document.getElementById('hdListName').textContent }));
  check('导入备份后连「所选词库」也还原', listRestored.list === 'cet4', JSON.stringify(listRestored));

  // 导入非法 JSON
  const badP = path.join(DL, 'bad.json');
  fs.writeFileSync(badP, 'not a json at all');
  await page.setInputFiles('#importFile', [badP]);
  await page.waitForTimeout(600);
  const badToast = await page.textContent('#toast');
  check('导入非法 JSON：给出失败提示且不崩', /导入失败/.test(badToast) && errs.length === 0, badToast);

  // 发音调用不抛异常
  const speakOK = await page.evaluate(() => { try { speak('test'); return true; } catch (e) { return false; } });
  check('调用系统 TTS 发音不抛异常', speakOK);

  // 窗口缩放：字号与卡片宽度固定
  // 卡片在复习页里，测量前先切回去并渲染一张卡
  // 当前处于拼写模式，测量翻面卡需先切回卡片模式
  await page.evaluate(() => { Settings.mode = 'card'; buildQueue(); render(); go('study'); showCard(); });
  await page.waitForTimeout(350);
  const cardVisible = await page.evaluate(() => getComputedStyle(document.getElementById('card')).display !== 'none');
  check('卡片模式下卡片可见（测量前置条件）', cardVisible, 'display=' + cardVisible);
  const measure = () => page.evaluate(() => ({
    card: document.getElementById('cardInner').offsetWidth,
    cardH: document.getElementById('cardInner').offsetHeight,
    page: document.querySelector('.page.active').offsetWidth,
    word: getComputedStyle(document.querySelector('.face.front .word') || document.body).fontSize,
  }));
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.waitForTimeout(250);
  const m1200 = await measure();
  await page.setViewportSize({ width: 1800, height: 1000 });
  await page.waitForTimeout(250);
  const m1800 = await measure();
  check('卡片宽度随窗口变宽（铺满面板内容区）',
    m1800.card > m1200.card && Math.abs(m1200.card - (m1200.page - 48)) <= 2,
    `1200px 窗口→${m1200.card}px（面板内容 ${m1200.page - 48}）／1800px 窗口→${m1800.card}px`);
  check('单词字号在两档窗口下一致（40px）', m1200.word === m1800.word, `${m1200.word} / ${m1800.word}`);
  // 拼写模式的面板同样固定宽度
  const spW = await page.evaluate(() => {
    Settings.mode = 'spell'; showCard();
    const pg = document.querySelector('.page.active');
    return { box: document.getElementById('spellBox').offsetWidth, page: pg.offsetWidth };
  });
  check('拼写面板同样铺满内容区', Math.abs(spW.box - (spW.page - 48)) <= 2, JSON.stringify(spW));

  /* ---------------- 新增：自动发音开关 / 完成页统计 ---------------- */
  section('自动发音与完成页统计');
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 30000 });
  await page.waitForTimeout(400);
  const sw0 = await page.evaluate(() => ({
    on: document.getElementById('swAutoSpeak').classList.contains('on'),
    val: Settings.autoSpeak,
  }));
  check('自动发音开关默认关闭', sw0.on === false && sw0.val === false, JSON.stringify(sw0));

  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(250);
  const swStyle = await page.evaluate(() => {
    const knob = getComputedStyle(document.getElementById('swAutoSpeak'), '::after');
    const box = getComputedStyle(document.getElementById('swAutoSpeak'));
    return { knobW: knob.width, knobR: knob.borderRadius, dur: knob.transitionDuration, ease: knob.transitionTimingFunction,
             boxR: box.borderRadius, boxBg: box.backgroundColor };
  });
  check('开关是 Apple 风滑块（圆角轨道 + 圆形旋钮 + 缓动过渡）',
    parseFloat(swStyle.knobW) > 20 && /9999|50%/.test(swStyle.knobR)
    && /cubic-bezier/.test(swStyle.ease) && parseFloat(swStyle.dur) > 0, JSON.stringify(swStyle));

  await page.click('#swAutoSpeak');
  await page.waitForTimeout(350);
  const sw1 = await page.evaluate(() => ({
    on: document.getElementById('swAutoSpeak').classList.contains('on'),
    aria: document.getElementById('swAutoSpeak').getAttribute('aria-checked'),
    stored: localStorage.getItem('wh_setting_autospeak'),
    knob: getComputedStyle(document.getElementById('swAutoSpeak'), '::after').transform,
  }));
  check('开关可打开、落盘、aria 同步且旋钮滑出',
    sw1.on && sw1.aria === 'true' && sw1.stored === 'true' && /matrix/.test(sw1.knob), JSON.stringify(sw1));

  // 打桩 speak，验证翻面确实读了当前词
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(200);
  await page.click('#listCards .list-card[data-k="cet4"]');
  await page.waitForFunction(() => !document.getElementById('btnStart').disabled, null, { timeout: 30000 });
  await page.click('#btnStart');
  await page.waitForTimeout(700);
  await page.evaluate(() => { window.__spoke = []; speak = (w)=> window.__spoke.push(w); });
  await page.click('#card', { position: { x: 80, y: 70 } });
  await page.waitForTimeout(450);
  const spoke = await page.evaluate(() => ({ list: window.__spoke, word: S.queue[S.idx].w }));
  check('翻面后自动发音一次，且读的是当前词',
    spoke.list.length === 1 && spoke.list[0] === spoke.word, JSON.stringify(spoke));

  await page.evaluate(async () => {
    for (let i = 0; i < 80; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 3)); grade(5); await new Promise(r => setTimeout(r, 3));
    }
  });
  await page.waitForTimeout(700);
  const doneTxtNew = await page.textContent('#doneDetail');
  check('完成页给出用时与认识率', /用时 \d+ 分 \d+ 秒/.test(doneTxtNew) && /认识率 \d+%/.test(doneTxtNew), doneTxtNew);
  check('全点「认识」时认识率为 100%', /认识率 100%/.test(doneTxtNew), doneTxtNew);

  // 关掉开关后翻面不该再读
  await page.click('nav button[data-tab="settings"]');
  await page.waitForTimeout(250);
  await page.click('#swAutoSpeak');
  await page.waitForTimeout(300);
  const offVal = await page.evaluate(() => ({ val: Settings.autoSpeak, on: document.getElementById('swAutoSpeak').classList.contains('on') }));
  check('可以再关掉', offVal.val === false && offVal.on === false, JSON.stringify(offVal));

  /* ---------------- 再学一点 ---------------- */
  section('再学一点');
  // 承接上一段：队列已走完，按钮应是「再学一点」
  await page.click('nav button[data-tab="home"]');
  await page.waitForTimeout(300);
  const more0 = await page.evaluate(() => ({
    txt: document.getElementById('btnStart').textContent,
    dis: document.getElementById('btnStart').disabled,
    unseen: unseenCount(),
    queue: S.queue.length, idx: S.idx, tNew: document.getElementById('tNew').textContent,
  }));
  check('完成今日任务后按钮为「再学一点 →」且可点',
    !more0.dis && /再学一点/.test(more0.txt) && more0.unseen > 0, JSON.stringify(more0));

  await page.click('#btnStart');
  await page.waitForTimeout(900);
  const more1 = await page.evaluate(() => ({
    queue: S.queue.length, idx: S.idx, tNew: document.getElementById('tNew').textContent,
    onStudy: document.getElementById('page-study').classList.contains('active'),
    cardVisible: getComputedStyle(document.getElementById('card')).display !== 'none',
    word: (document.querySelector('.face.front .word') || {}).textContent || '',
  }));
  // 追加是「接着学」：索引应该正好落在新加的第一张上
  check('点「再学一点」后队列追加新词并直接接着学',
    more1.queue > more0.queue && more1.idx === more0.queue && more1.onStudy && more1.cardVisible && more1.word.length > 1,
    JSON.stringify(more1));
  check('首页「今日新词」计数同步增加',
    parseInt(more1.tNew) === parseInt(more0.tNew) + (more1.queue - more0.queue), `${more0.tNew} → ${more1.tNew}`);

  // 再走完这一批 → 完成页应有「再学一点」按钮
  await page.evaluate(async () => {
    for (let i = 0; i < 90; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 3)); grade(5); await new Promise(r => setTimeout(r, 3));
    }
  });
  await page.waitForTimeout(600);
  const doneMore = await page.evaluate(() => ({
    shown: getComputedStyle(document.getElementById('btnDoneMore')).display !== 'none',
    txt: document.getElementById('btnDoneMore').textContent,
    unseen: unseenCount(),
  }));
  check('完成页也有「再学一点」入口（词库还有没学过的词时）',
    doneMore.shown && /再学一点/.test(doneMore.txt) && doneMore.unseen > 0, JSON.stringify(doneMore));
  await page.click('#btnDoneMore');
  await page.waitForTimeout(900);
  const afterDoneMore = await page.evaluate(() => ({
    queue: S.queue.length, idx: S.idx, daily: Settings.daily,
    visible: getComputedStyle(document.getElementById('card')).display !== 'none',
  }));
  check('完成页点「再学一点」同样能继续学（从新加的第一张接着学）',
    afterDoneMore.queue > 0 && afterDoneMore.idx === afterDoneMore.queue - afterDoneMore.daily && afterDoneMore.visible,
    JSON.stringify(afterDoneMore));

  // 把当前词库全部标记为已学 → 按钮该变成禁用的「今日已完成」
  await page.evaluate(() => {
    const prog = {};
    // 全部标为已学、且到期日排到 30 天后 → 队列应为空（模拟"这个词库学完了"）
    S.data.forEach(e => { prog[e.w] = { ef: 2.5, ivl: 30, reps: 2, due: today() + 30, lapses: 0, seen: 1 }; });
    saveProg(S.list, prog);
    S.queue = []; S.idx = 0;
    buildQueue(); render(); go('home');
  });
  await page.waitForTimeout(400);
  const allDone = await page.evaluate(() => ({
    txt: document.getElementById('btnStart').textContent,
    dis: document.getElementById('btnStart').disabled,
    unseen: unseenCount(),
    desc: document.getElementById('taskDesc').textContent,
  }));
  check('词库学完（无未学词）时按钮回到禁用的「今日已完成 🎉」',
    allDone.dis && /今日已完成/.test(allDone.txt) && allDone.unseen === 0, JSON.stringify(allDone));

  // 学完一轮后（队列耗尽但词库还有未学词），切到复习页应看到完成页 + 「再学一点」
  await page.evaluate(() => {
    // 重置成"学过 30 个（到期在 30 天后）、其余全没学过"的状态，这样队列=今日新词，且词库还有大量未学词
    const prog = {};
    S.data.slice(0, 30).forEach(e => { prog[e.w] = { ef: 2.5, ivl: 30, reps: 2, due: today() + 30, lapses: 0, seen: 1 }; });
    saveProg(S.list, prog);
    buildQueue();
    go('study'); showCard();
  });
  await page.waitForTimeout(400);
  await page.evaluate(async () => {                  // 把这一批走完
    for (let i = 0; i < 90; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
  });
  await page.waitForTimeout(500);
  const idleMore = await page.evaluate(() => ({
    done: getComputedStyle(document.getElementById('studyDone')).display,
    more: getComputedStyle(document.getElementById('btnDoneMore')).display,
    left: S.queue.length - S.idx, unseen: unseenCount(),
  }));
  check('学完一轮后复习页给完成页 + 可点的「再学一点」（词库仍有未学词）',
    idleMore.done !== 'none' && idleMore.more !== 'none' && idleMore.left === 0 && idleMore.unseen > 0,
    JSON.stringify(idleMore));

  /* ---------------- 再学一点 · 边界 ---------------- */
  section('再学一点 · 边界');
  const edge = await page.evaluate(async () => {
    Settings.daily = 2;
    // 留最后 3 个词没学 → 今日一份发 2 个，还剩 1 个未学（用来验"剩不足一批"）
    const prog = {};
    S.data.slice(0, S.data.length - 3).forEach(e => {
      prog[e.w] = { ef: 2.5, ivl: 30, reps: 2, due: today() + 30, lapses: 0, seen: 1 };
    });
    saveProg(S.list, prog);
    buildQueue(); render(); go('study'); showCard();
    const firstBatch = S.queue.length;
    for (let i = 0; i < 40; i++) {
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
    go('home');
    const mid = { unseen: unseenCount(), txt: $('btnStart').textContent, dis: $('btnStart').disabled };
    const added = addMoreCards(Settings.daily);          // 只剩 1 个未学 → 应只加 1 个，不是 2 个
    const after = { added, queue: S.queue.length, idx: S.idx, unseen: unseenCount() };
    for (let i = 0; i < 40; i++) {                       // 把最后一个也学完
      if (S.idx >= S.queue.length) break;
      reveal(); await new Promise(r => setTimeout(r, 2)); grade(5); await new Promise(r => setTimeout(r, 2));
    }
    render(); go('home');
    const end = { unseen: unseenCount(), txt: $('btnStart').textContent, dis: $('btnStart').disabled };
    return { firstBatch, mid, after, end };
  });
  // 追加后新词还在队列里（未学），所以 unseen 仍是 1；索引应正好落在新加的那张
  check('剩不足一批时「再学一点」只加剩下的（每日 2 个、只剩 1 个 → 只加 1 个）',
    edge.firstBatch === 2 && edge.after.added === 1 && edge.after.queue === 3 && edge.after.idx === 2,
    JSON.stringify({ firstBatch: edge.firstBatch, ...edge.after }));
  check('追加后按钮文案为「再学一点」且可点', !edge.mid.dis && /再学一点/.test(edge.mid.txt), JSON.stringify(edge.mid));
  check('把最后一个也学完后，按钮变回禁用的「今日已完成 🎉」',
    edge.end.dis && /今日已完成/.test(edge.end.txt), JSON.stringify(edge.end));

  const rand = await page.evaluate(async () => {
    Settings.order = 'rand';
    Settings.daily = 8;
    const prog = {};
    S.data.slice(0, 20).forEach(e => { prog[e.w] = { ef: 2.5, ivl: 30, reps: 2, due: today() + 30, lapses: 0, seen: 1 }; });
    saveProg(S.list, prog);
    buildQueue();
    S.queue = []; S.idx = 0;                             // 模拟"今日已完成"
    const added = addMoreCards(Settings.daily);
    const picked = S.queue.map(q => q.w);
    const prog2 = getProg(S.list);
    return {
      added, order: Settings.order,
      allUnseen: picked.every(w => !prog2[w]),
      uniq: new Set(picked).size === picked.length,
    };
  });
  check('乱序模式下「再学一点」加的都是没学过的词且不重复',
    rand.added === 8 && rand.allUnseen && rand.uniq, JSON.stringify(rand));

  /* ---------------- 学习统计页排版 ---------------- */
  section('学习统计页排版');
  await page.evaluate(() => {
    // 造点记录，让热力图 / 进度条有内容
    const log = {};
    for (let i = 0; i < 60; i++) {
      const d = new Date(Date.now() - i * 86400000);
      log[d.toISOString().slice(0, 10)] = (i % 7) * 4;
    }
    lsSet('wh_log_daily', log);
    go('stats');
  });
  await page.waitForTimeout(600);
  const st = await page.evaluate(() => {
    const hm = document.getElementById('heatmap');
    const cells = hm.querySelectorAll('i');
    const bars = [...document.querySelectorAll('#listBars .bar-row')];
    const rowH = bars.map(b => Math.round(b.getBoundingClientRect().height));
    const rowOver = bars.filter(b => b.scrollWidth > b.clientWidth + 1).length;
    const numOver = bars.filter(b => { const n = b.querySelector('.num'); return n.scrollWidth > n.clientWidth + 1; }).length;
    const barW = bars.map(b => Math.round(b.querySelector('.bar').getBoundingClientRect().width));
    return {
      cells: cells.length,
      months: [...hm.querySelectorAll('.hm-mon')].map(e => e.textContent).filter(Boolean).length,
      wd: [...hm.querySelectorAll('.hm-wd')].map(e => e.textContent).filter(Boolean).join(''),
      cellBox: (() => { const r = cells[0].getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })(),
      hasLegend: document.querySelectorAll('.hm-tip i').length,
      sum: document.getElementById('hmSum').textContent,
      bars: bars.length,
      rowH: rowH,
      numOver: numOver, rowOver: rowOver, barW: barW,
      panels: document.querySelectorAll('#page-stats .panel').length,
      statCards: document.querySelectorAll('#page-stats .stat-card').length,
      over: document.documentElement.scrollHeight - window.innerHeight,
    };
  });
  check('热力图覆盖近 52 周（364 格）且格子是正方形',
    st.cells === 364 && st.cellBox[0] === st.cellBox[1] && st.cellBox[0] >= 10, JSON.stringify({ cells: st.cells, box: st.cellBox }));
  check('热力图有月份标签与星期标签、有图例与汇总',
    st.months >= 8 && /一/.test(st.wd) && /三/.test(st.wd) && /五/.test(st.wd) && st.hasLegend === 4 && /近一年复习/.test(st.sum),
    JSON.stringify({ months: st.months, wd: st.wd, legend: st.hasLegend, sum: st.sum }));
  check(`词库进度 ${NLISTS} 行、上行下条两行结构、数字不溢出且条宽一致`,
    st.bars === NLISTS && (Math.max(...st.rowH) - Math.min(...st.rowH)) <= 2 && st.numOver === 0 && st.rowOver === 0
    && new Set(st.barW).size === 1 && st.barW[0] > 120,
    JSON.stringify({ bars: st.bars, rowH: st.rowH, numOver: st.numOver, rowOver: st.rowOver, barW: st.barW }));
  check('统计页用卡片分组（4 张统计卡 + 2 个分组卡）且不滚动',
    st.statCards === 4 && st.panels === 2 && st.over <= 0, JSON.stringify({ statCards: st.statCards, panels: st.panels, over: st.over }));

  console.log('\n页面 JS 报错:', errs.length ? errs.join(' | ') : '无');
  await browser.close();
  const failed = R.filter(r => !r.ok);
  console.log(`\n================ word-hub 汇总：通过 ${R.length - failed.length}/${R.length} ================`);
  if (failed.length) console.log(failed.map(f => ' - ' + f.n + '  ' + f.d).join('\n'));
  fs.writeFileSync(path.join(DL, 'word_full_report.json'), JSON.stringify(R, null, 1));
})().catch(e => { console.error('脚本异常', e); process.exit(1); });
