const $ = (id) => document.getElementById(id);
const managedMode = new URLSearchParams(window.location.search).get('managed') === '1';
const sessionKey = managedMode ? 'platformTenantSession' : 'tenantWorkbenchSession';
const workbench = JSON.parse(sessionStorage.getItem(sessionKey) || 'null');
let activeHandoff = null;
const statusLabels = { pending: '待处理', in_progress: '处理中', awaiting_confirmation: '等待客户确认结束', ended: '已结束' };
const templates = [
  '您好，我是人工客服，很高兴为您服务。',
  '您好，您的问题我已经收到，正在为您核实，请稍候。',
  '请问您方便补充一下课程名称和上课日期吗？',
  '感谢您的耐心等待，相关信息已经为您核实完成。',
  '还有其他问题需要我协助处理吗？',
  '感谢您的咨询，祝您和孩子学习愉快！',
];

if (!workbench) window.location.replace(managedMode ? '/ui/platform.html' : '/ui/tenant-auth.html?mode=login');
if (workbench) {
  $('agent-title').textContent = `${workbench.tenant_name}·人工客服`;
  $('agent-brand').textContent = workbench.tenant_name;
  $('back-to-tenant').href = `/ui/tenant.html${managedMode ? '?managed=1' : ''}`;
}

function handoffCard(item) {
  const button = document.createElement('button'); button.type = 'button'; button.className = `handoff-ticket ${activeHandoff?.id === item.id ? 'active' : ''}`;
  const state = document.createElement('span'); state.className = `state-badge handoff-${item.status}`; state.textContent = statusLabels[item.status] || item.status;
  const summary = document.createElement('strong'); summary.textContent = item.summary || '客户请求人工协助';
  const meta = document.createElement('small'); meta.textContent = `会话 ${item.conversation_id.slice(0, 8)} · ${new Date(item.created_at).toLocaleString('zh-CN')}`;
  button.append(state, summary, meta); button.addEventListener('click', async () => { activeHandoff = item; await renderThread(); await loadHandoffs(); }); return button;
}

async function loadHandoffs() {
  if (!workbench) return;
  try {
    const data = await Demo.api('/admin/handoffs', { token: workbench.access_token });
    const rows = data.handoffs || []; const list = $('handoff-list'); list.replaceChildren();
    $('pending-count').textContent = rows.filter((item) => item.status === 'pending').length;
    if (!rows.length) list.innerHTML = '<div class="empty compact">暂无人工会话。</div>';
    rows.forEach((item) => list.append(handoffCard(item)));
    if (activeHandoff) activeHandoff = rows.find((item) => item.id === activeHandoff.id) || null;
  } catch (error) { Demo.setStatus($('handoff-list-status'), error.message, true); }
}

async function renderThread() {
  if (!activeHandoff) return;
  $('thread-title').textContent = activeHandoff.summary || '人工会话';
  $('thread-state').textContent = `状态：${statusLabels[activeHandoff.status] || activeHandoff.status}`;
  const ended = activeHandoff.status === 'ended';
  $('accept-handoff').disabled = ended || activeHandoff.status !== 'pending';
  $('request-close').disabled = ended || activeHandoff.status === 'awaiting_confirmation';
  $('agent-reply-input').disabled = ended;
  $('agent-reply-form').querySelector('button').disabled = ended;
  const data = await Demo.api(`/conversations/${activeHandoff.conversation_id}/messages`, { token: workbench.access_token });
  Demo.renderMessages($('agent-messages'), data.messages || []);
}

$('refresh-handoffs').addEventListener('click', loadHandoffs);
$('accept-handoff').addEventListener('click', async () => { activeHandoff = await Demo.api(`/admin/handoffs/${activeHandoff.id}/accept`, { method: 'POST', token: workbench.access_token }); await Promise.all([loadHandoffs(), renderThread()]); });
$('request-close').addEventListener('click', async () => { activeHandoff = await Demo.api(`/admin/handoffs/${activeHandoff.id}/request-close`, { method: 'POST', token: workbench.access_token }); Demo.setStatus($('agent-status'), '已请求客户确认结束；10 分钟未回应将自动结束。'); await Promise.all([loadHandoffs(), renderThread()]); });
$('agent-reply-form').addEventListener('submit', async (event) => { event.preventDefault(); const content = $('agent-reply-input').value.trim(); if (!activeHandoff || !content) return; try { activeHandoff = await Demo.api(`/admin/handoffs/${activeHandoff.id}/reply`, { method: 'POST', token: workbench.access_token, body: JSON.stringify({ content }) }); $('agent-reply-input').value = ''; await Promise.all([loadHandoffs(), renderThread()]); Demo.setStatus($('agent-status'), '人工回复已发送。'); } catch (error) { Demo.setStatus($('agent-status'), error.message, true); } });

templates.forEach((text) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'suggestion-chip'; button.textContent = text; button.addEventListener('click', () => { $('agent-reply-input').value = text; $('agent-reply-input').focus(); }); $('reply-templates').append(button); });
loadHandoffs(); setInterval(async () => { await loadHandoffs(); if (activeHandoff) await renderThread(); }, 2000);
