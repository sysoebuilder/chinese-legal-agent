// 启动 Vite 或前端容器后运行。拦截 /v1，不调用真实模型或工具。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.CHAT_TEST_URL || 'http://127.0.0.1:5173';
const output = path.resolve(__dirname,'../data/chat-preview');
fs.mkdirSync(output,{ recursive:true });
const A = '10000000-0000-4000-8000-000000000001';
const B = '10000000-0000-4000-8000-000000000002';
const iso = new Date().toISOString();
const sessions = [{ thread_id:A,name:'理解 Agent 的调用链',updated_at:iso,created_at:iso }];
const histories = { [A]: Array.from({ length:22 },(_,i) => ({ id:`history-${i}`,role:i%2 ? 'assistant' : 'user',content:i%2 ? '从输入到回答，经历 **API → Redis → Worker → SSE**。\n\n<script>window.injected=true</script><img src=x onerror="window.injected=true">' : '帮我解释这一轮 Agent 对话的调用链。' })) };
let run=null, mode='answer', runNumber=0, resumed=false, cancelled=false, requireToken=false, authObserved=false, reconnectStarted=false, reconnectCursor=null;
let workerBusy=true, checkpointAvailable=true, checkpointClicks=0;
let recoveryCalls=0;
const errors = [];
const event = (revision,name,data) => {
  if (name.startsWith('run.') || name === 'interrupt') {
    data = { ...run, ...data, revision, status:name === 'interrupt' ? data.status : name.slice(4) };
    name = 'run.state';
  }
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
};
const frames = [];
function answerFrames() {
  return [event(1,'run.running',{}),event(2,'reasoning.delta',{ message_id:'thought',generation:1,text:'先梳理问题，再一步步说明。' }),event(3,'answer.delta',{ message_id:'tool-intro',generation:1,text:'先执行查询。' }),event(4,'answer.delta',{ message_id:'answer',generation:1,text:'这是实时回答' }),event(5,'run.succeeded',{ answer:'这是**最终回答**。\n\n- 已接上实时事件\n- 支持历史会话\n\n```python\nprint("hello")\n```' })];
}
(async () => {
  const browser = await chromium.launch({ headless:true });
  try {
    const context = await browser.newContext({ viewport:{ width:1440,height:960 },colorScheme:'light' });
    const page = await context.newPage();
    page.on('pageerror',error => errors.push(error.message));
    page.on('response',response => { if (response.url().includes('/assets/') && response.status() >= 400) errors.push(`asset ${response.status()} ${response.url()}`); });
    await page.route('**/v1/**',async route => {
      const request = route.request(); const url = new URL(request.url()); const pathname = url.pathname;
      const send = (body,status=200) => route.fulfill({ status,contentType:'application/json',body:JSON.stringify(body) });
      if (requireToken && request.headers().authorization !== 'Bearer browser-test-token') return send({ detail:'需要有效的 Bearer token' },401);
      if (requireToken) authObserved = true;
      if (pathname === '/v1/config') return send({ modelconfig:{ active:'glm',models:{ glm:{ provider:'glm',model:'测试模型',base_url:'https://model.example',api_key_ref:'ZAI_API_KEY' } } },mouldesconfig:{ legal:{ able:true },plugins:{} },secretsconfig:{ keys:{ ZAI_API_KEY:true } },permissionsconfig:{ mode:'unrestricted' } });
      if (pathname === '/v1/sessions') return send({ sessions,has_more:false,next_offset:null });
      if (pathname.endsWith('/recovery')) {
        recoveryCalls++;
        const activeRun = run?.thread_id === pathname.split('/')[3] && !['succeeded','cancelled'].includes(run.status) ? run : null;
        return send({ run:activeRun,can_reconnect:!!activeRun && mode==='worker_loss' && !workerBusy && checkpointAvailable,busy:!!activeRun && workerBusy,pending_nodes:activeRun && checkpointAvailable ? ['general_agent'] : [] });
      }
      if (pathname.endsWith('/messages')) {
        const thread = pathname.split('/')[3]; const messages = histories[thread] || [];
        const older = url.searchParams.has('before');
        return send({ thread_id:thread,messages:older ? messages.slice(0,-20) : messages.slice(-20),has_more:!older && messages.length>20,next_before:messages.length>20 ? messages[messages.length-20].id : null });
      }
      if (pathname === '/v1/runs' && request.method() === 'POST') {
        const body = request.postDataJSON();
        if (body.message === '模拟连接错误') return send({ detail:'服务暂时不可用' },503);
        const thread = body.thread_id || `10000000-0000-4000-8000-${String(runNumber+2).padStart(12,'0')}`; runNumber++;
        run = { run_id:`20000000-0000-4000-8000-${String(runNumber).padStart(12,'0')}`,thread_id:thread,status:'queued',provider:'test',model:'测试模型',cancel_requested:false,interrupts:[],answer:null,error:null,generation:1,revision:0,created_at:iso,updated_at:iso };
        histories[thread] ||= []; histories[thread].push({ id:`run:${run.run_id}:user`,role:'user',content:body.message });
        if (!sessions.some(s => s.thread_id === thread)) sessions.unshift({ thread_id:thread,name:body.message,created_at:iso,updated_at:iso });
        resumed=false; cancelled=false; frames.length=0;
        if (mode==='worker_loss') frames.push(event(1,'run.running',{ generation:1 }));
        else if (['answer','reconnect'].includes(mode)) frames.push(...answerFrames());
        else if (mode === 'failure') frames.push(event(1,'run.running',{}),event(2,'run.failed',{ error:'TemporaryError' }));
        else frames.push(event(1,'run.running',{}),event(2,'interrupt',{ status:mode==='approval' ? 'waiting_approval' : 'waiting_input',interrupts:[{ id:'review-1',value:mode==='approval' ? { action_requests:[{ name:'send_email',args:{ recipient:'example@example.com',subject:'测试' } }] } : { type:'legal_information_required',question:'请补充合同签订日期。' } }] }));
        return send(run,202);
      }
      if (pathname.endsWith('/reconnect')) {
        assert.equal(mode,'worker_loss'); assert.ok(!workerBusy && checkpointAvailable);
        assert.deepEqual(request.postDataJSON(),{}); checkpointClicks++; resumed=true;
        run.status='queued'; run.generation++; run.revision++; frames.length=0;
        frames.push(event(run.revision+1,'run.running',{}),event(0,'answer.delta',{ generation:run.generation,text:'继续' }),event(run.revision+2,'run.succeeded',{ answer:'已收到补充信息，继续完成回答。' }));
        return send(run,202);
      }
      if (pathname.endsWith('/resume')) {
        const body = request.postDataJSON();
        if (mode === 'approval') assert.deepEqual(body.responses,{ 'review-1':{ decisions:[{ type:'reject' }] } });
        if (mode === 'input') assert.deepEqual(body.responses,{ 'review-1':{ answer:'2026 年 10 月 6 日' } });
        resumed=true; run.status='queued'; run.interrupts=[]; run.generation++; run.revision++; frames.length=0;
        frames.push(event(run.revision+1,'run.running',{}),event(0,'answer.delta',{ message_id:'resumed',generation:run.generation,text:'继续' }),event(run.revision+2,'run.succeeded',{ answer:'已收到补充信息，继续完成回答。' }));
        return send(run,202);
      }
      if (pathname.endsWith('/cancel')) { cancelled=true; run.status='cancelled'; run.interrupts=[]; run.revision++; frames.length=0; frames.push(event(run.revision,'run.cancelled',{})); return send(run); }
      if (pathname.endsWith('/events')) {
        assert.equal(url.searchParams.has('after'),false);
        if (mode==='reconnect' && !reconnectStarted) { reconnectStarted=true; run.status='running'; run.revision=1; return route.fulfill({ contentType:'text/event-stream',body:frames.slice(0,2).join('') }); }
        if (mode==='reconnect') reconnectCursor=url.searchParams.get('after');
        if (cancelled) run.status='cancelled';
        else if (['answer','reconnect'].includes(mode) || resumed) {
          run.status='succeeded'; run.answer=['answer','reconnect'].includes(mode) ? JSON.parse(frames.at(-1).split('data: ')[1]).answer : '已收到补充信息，继续完成回答。';
          if (!histories[run.thread_id].some(m => m.id === `answer-${run.run_id}`)) histories[run.thread_id].push({ id:`answer-${run.run_id}`,role:'assistant',content:run.answer });
        } else if (mode==='worker_loss') { run.status='running'; }
        else if (mode==='failure') { run.status='failed'; run.error='TemporaryError'; }
        else { run.status=mode==='approval' ? 'waiting_approval' : 'waiting_input'; run.interrupts=[{ id:'review-1',value:mode==='approval' ? { action_requests:[{ name:'send_email',args:{ recipient:'example@example.com',subject:'测试' } }] } : { type:'legal_information_required',question:'请补充合同签订日期。' } }]; }
        run.revision=Math.max(run.revision,...frames.map(frame => JSON.parse(frame.split('data: ')[1]).revision || 0));
        return route.fulfill({ contentType:'text/event-stream',body:(mode==='reconnect' ? frames.slice(2) : frames).join('') || ': ping\n\n' });
      }
      if (pathname === `/v1/runs/${run?.run_id}`) return send(run);
      return send({ detail:'不存在' },404);
    });
    await page.goto(origin); await page.getByRole('button',{ name:'理解 Agent 的调用链',exact:true }).waitFor();
    await page.screenshot({ path:path.join(output,'desktop-empty.png') });
    await page.getByRole('button',{ name:'理解 Agent 的调用链',exact:true }).click();
    await page.waitForFunction(() => document.querySelectorAll('#messages .message').length===20);
    await page.getByRole('button',{ name:'加载更早的对话' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#messages .message').length===22);
    assert.equal(await page.evaluate(() => window.injected),undefined);
    await page.locator('#new-chat').click();
    await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('帮我梳理一下 SSE 的工作方式');
    await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.waitForFunction(() => document.querySelector('#messages .assistant')?.textContent.includes('最终回答'));
    assert.equal(await page.locator('#messages .message').count(),2);
    assert.ok(!(await page.locator('#messages').innerText()).includes('先执行查询'));
    await page.locator('#messages .reasoning-toggle').click();
    assert.ok((await page.locator('#messages .reasoning-content').innerText()).includes('先梳理问题'));
    const assistantBox = await page.locator('#messages .assistant .message-main').boundingBox();
    const userBox = await page.locator('#messages .user .message-main').boundingBox(); assert.ok(userBox.x>assistantBox.x);
    await page.waitForTimeout(300);
    await page.screenshot({ path:path.join(output,'desktop-chat.png') });
    await page.getByRole('button',{ name:'切换深浅色' }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path:path.join(output,'desktop-dark.png') });
    await page.getByRole('button',{ name:'切换深浅色' }).click();

    mode='approval'; await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('请发送测试邮件'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.getByText('请确认接下来的操作',{ exact:true }).waitFor();
    assert.equal(await page.getByRole('button',{ name:'同意',exact:true }).getAttribute('aria-pressed'),'false');
    await page.getByRole('button',{ name:'理解 Agent 的调用链',exact:true }).click();
    await page.getByRole('button',{ name:'帮我梳理一下 SSE 的工作方式',exact:true }).click();
    await page.getByRole('button',{ name:'拒绝',exact:true }).click();
    await page.waitForFunction(() => document.querySelector('#messages').textContent.includes('已收到补充信息'));
    assert.ok(resumed);

    mode='input'; await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('请分析合同'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.getByLabel('请补充合同签订日期。').fill('2026 年 10 月 6 日'); await page.getByRole('button',{ name:'提交并继续' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#messages .message').length===6);

    mode='approval'; await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('停止测试'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.getByRole('button',{ name:'同意',exact:true }).waitFor(); await page.getByRole('button',{ name:'停止生成',exact:true }).click();
    await page.waitForFunction(() => document.getElementById('run-status').textContent.includes('已停止')); assert.ok(cancelled);
    await page.locator('#new-chat').click();
    mode='reconnect'; await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('测试事件流重新连接'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.waitForFunction(() => document.querySelector('#messages .assistant')?.textContent.includes('最终回答'));
    assert.equal(reconnectCursor,null);
    await page.locator('#messages .reasoning-toggle').click();
    assert.equal(await page.locator('#messages .reasoning-content').textContent(),'先梳理问题，再一步步说明。');
    await page.locator('#new-chat').click();
    mode='failure'; await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('测试失败恢复'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.getByRole('button',{ name:'重试本轮' }).click();
    await page.waitForFunction(() => document.querySelector('#messages .assistant')?.textContent.includes('已收到补充信息'));
    assert.equal(await page.locator('#messages .user').count(),1);
    await page.locator('#new-chat').click();
    mode='worker_loss'; workerBusy=true; checkpointAvailable=true;
    await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('测试 Worker 退出后断点重连');
    await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.waitForFunction(() => document.getElementById('run-status').textContent.includes('正在思考'));
    assert.equal(await page.getByRole('button',{ name:'从断点重连' }).count(),0);
    await page.getByRole('button',{ name:'检查断点',exact:true }).click();
    await page.getByText('Worker 仍在执行，请等待',{ exact:true }).waitFor();
    await page.waitForTimeout(300);
    const beforeIdleChecks=recoveryCalls;
    await page.waitForTimeout(5200);
    assert.equal(recoveryCalls,beforeIdleChecks,'断点检查不能按时间间隔重复请求');
    workerBusy=false; checkpointAvailable=false;
    await page.getByRole('button',{ name:'检查断点',exact:true }).click();
    await page.getByText('当前没有可恢复的断点',{ exact:true }).waitFor();
    assert.equal(await page.getByRole('button',{ name:'从断点重连' }).count(),0);
    checkpointAvailable=true;
    await page.getByRole('button',{ name:'检查断点',exact:true }).click();
    await page.getByRole('button',{ name:'从断点重连' }).waitFor();
    // 清除任务缓存再刷新，仍然从服务端找到运行与 checkpoint。
    await page.evaluate(() => localStorage.removeItem('agent100.runs'));
    await page.reload();
    await page.getByRole('button',{ name:'从断点重连' }).click();
    await page.waitForFunction(() => document.querySelector('#messages .assistant')?.textContent.includes('已收到补充信息'));
    assert.equal(checkpointClicks,1); assert.equal(await page.locator('#messages .user').count(),1);
    await page.locator('#new-chat').click();
    await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('模拟连接错误'); await page.getByRole('button',{ name:'发送消息',exact:true }).click();
    await page.getByText('服务暂时不可用',{ exact:true }).waitFor(); assert.equal(await page.getByRole('textbox',{ name:'发送消息',exact:true }).inputValue(),'模拟连接错误');

    await page.setViewportSize({ width:390,height:844 });
    await page.getByRole('button',{ name:'切换历史对话栏' }).click();
    assert.ok(await page.locator('#sidebar').isVisible()); await page.getByRole('button',{ name:'关闭侧栏',exact:true }).click();
    await page.waitForFunction(() => document.getElementById('sidebar').getBoundingClientRect().right <= 0);
    assert.equal(await page.locator('#sidebar').getAttribute('aria-hidden'),'true');
    await page.getByRole('button',{ name:'关闭提示' }).click(); await page.getByRole('textbox',{ name:'发送消息',exact:true }).fill('');
    await page.waitForTimeout(300);
    await page.screenshot({ path:path.join(output,'mobile-empty.png') });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth));

    requireToken=true; await page.getByRole('button',{ name:'切换历史对话栏' }).click(); await page.getByRole('button',{ name:'刷新历史对话' }).click();
    await page.getByLabel('API 访问令牌').fill('browser-test-token'); await page.getByRole('button',{ name:'保存并连接' }).click();
    await page.waitForFunction(() => !document.getElementById('session-error').hidden===false);
    assert.ok(authObserved); assert.deepEqual(errors,[]);
    console.log('Browser checks passed: history pagination, safe Markdown, final answer/reasoning, layout, dark/mobile, session restoration, approval, input, cancel, reconnect without delta replay, failure recovery, on-demand checkpoint reconnect/lock/no-checkpoint/refresh/no-polling, error, auth.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
