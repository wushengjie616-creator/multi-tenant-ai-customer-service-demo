let tenantSession = null;
let tenantActiveHandoff = null;
let selectedFiles = [];
const $ = (id) => document.getElementById(id);
const featureMeta = {
  knowledge: ["📚", "知识库"], assistant: ["🤖", "AI 客服"], learning: ["🎓", "学习中心"],
  finance: ["🧾", "财务中心"], services: ["⚙️", "办事服务"], reminders: ["⏰", "提醒中心"],
  handoff: ["🎧", "人工服务"], operations: ["🛡️", "运营中心"],
};
const tenantSeed = Date.now().toString().slice(-6);
function fileStem(name) { return name.replace(/\.(md|markdown|txt)$/i, ""); }
function documentIdFor(name, index = 0) { return fileStem(name).toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5_-]+/g, "-").replace(/^-+|-+$/g, "") || `document-${index + 1}`; }

function renderEnabledFeatures(features) {
  const box = $("enabled-features"); box.replaceChildren();
  features.forEach((feature) => { const item = document.createElement("div"); item.className = "enabled-feature"; const icon = document.createElement("span"); icon.textContent = featureMeta[feature]?.[0] || "✓"; const name = document.createElement("strong"); name.textContent = featureMeta[feature]?.[1] || feature; const state = document.createElement("small"); state.textContent = "已启用"; item.append(icon, name, state); box.append(item); });
  $("enabled-count").textContent = features.length;
  const knowledgeColumn = document.querySelector("#knowledge-workspace > div");
  knowledgeColumn.hidden = !features.includes("knowledge");
  $("assistant-workspace").hidden = !features.includes("assistant");
  $("knowledge-workspace").hidden = !features.includes("knowledge") && !features.includes("assistant");
  $("knowledge-workspace").classList.toggle("single-column", !features.includes("knowledge") || !features.includes("assistant"));
  $("operations").hidden = !features.includes("operations");
  $("agent-workspace").hidden = !features.includes("handoff");
  $("tenant-handoff-monitor").hidden = !(features.includes("assistant") && features.includes("handoff"));
}

async function enterWorkbench(data) {
  tenantSession = { adminToken: data.access_token, token: data.customer_access_token, tenantId: data.tenant_id, userId: data.customer_user_id, conversationId: data.customer_conversation_id, adminConversationId: data.conversation_id, features: data.features, liveHandoff: data.features.includes("handoff"), humanMode: false, onHandoff: loadTenantHandoff };
  sessionStorage.setItem("tenantCustomerSession", JSON.stringify({ token: data.customer_access_token, tenantId: data.tenant_id, tenantName: data.tenant_name, customerName: data.customer_name, userId: data.customer_user_id, conversationId: data.customer_conversation_id, features: data.features, dependencyMode: data.dependency_mode }));
  $("workspace-title").textContent = `${data.tenant_name}·工作台`; $("workspace-identity").textContent = `租户 ID：${data.tenant_id} · 依赖模式：${data.dependency_mode}`;
  $("assistant-session-label").textContent = `模拟客户会话 · ${data.customer_name || "演示客户"} · ${data.dependency_mode === "configured" ? "真实 LLM / 租户 RAG" : "确定性 Mock"}`;
  renderEnabledFeatures(data.features);
  await Promise.all([loadCustomers(), data.features.includes("knowledge") ? loadDocuments() : null, data.features.includes("assistant") ? Demo.loadMessages(tenantSession, $("messages")) : null, data.features.includes("handoff") ? loadTenantHandoff() : null, data.features.includes("operations") ? loadOperations() : null, loadUsage()]);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

const agentWorkspace = document.createElement("section");
agentWorkspace.id = "agent-workspace"; agentWorkspace.className = "panel section-panel"; agentWorkspace.hidden = true;
agentWorkspace.innerHTML = `<div class="section-heading"><div><span class="eyebrow">HUMAN AGENT</span><h2>人工客服中心</h2><p class="hint">进入独立客服窗口处理待接入会话、使用话术模板回复，并发起会话结束确认。</p></div><button id="open-agent-workspace" class="button">进入人工客服窗口</button></div>`;
$("operations").before(agentWorkspace);
$("open-agent-workspace").addEventListener("click", () => { window.location.href = `/ui/agent.html${new URLSearchParams(window.location.search).get("managed") === "1" ? "?managed=1" : ""}`; });
const adminWorkspace = document.createElement("section"); adminWorkspace.className = "panel section-panel";
adminWorkspace.innerHTML = `<div class="section-heading"><div><span class="eyebrow">ADMIN ACCOUNTS</span><h2>新增租户管理员</h2><p class="hint">创建后会生成可复制的初始登录信息。初始密码只在本次页面中显示。</p></div></div><form id="admin-create-form" class="form-grid two-fields"><label>管理员姓名<input id="new-admin-name" value="运营管理员" required></label><label>登录邮箱<input id="new-admin-email" type="email" required></label><label>初始密码<input id="new-admin-password" value="AdminStart123!" minlength="8" required></label><button class="button" type="submit">创建管理员</button></form><div id="admin-login-card" class="result-card" hidden></div><div id="admin-create-status" class="status"></div>`;
agentWorkspace.before(adminWorkspace);
const usageWorkspace = document.createElement("section"); usageWorkspace.className = "panel section-panel";
usageWorkspace.innerHTML = '<div class="section-heading"><div><span class="eyebrow">LLM COST</span><h2>AI 会话 Token 成本</h2><p class="hint">按当前租户隔离统计；成本使用 .env 中配置的每百万 token 单价。</p></div><button id="refresh-usage" class="button secondary">刷新成本</button></div><div id="usage-list" class="data-list"><div class="empty compact">暂无 LLM 调用记录。</div></div><div id="usage-status" class="status"></div>';
adminWorkspace.before(usageWorkspace);
$("new-admin-email").value = `operator-${tenantSeed}@demo.example.com`;

function renderDocuments(documents) {
  const container = $("knowledge-list"); container.replaceChildren(); $("document-count").textContent = `${documents.length} 篇`;
  if (!documents.length) { const empty = document.createElement("div"); empty.className = "empty compact"; empty.textContent = "当前租户还没有文档。"; container.append(empty); return; }
  documents.forEach((doc) => { const card = document.createElement("article"); card.className = "document-card"; card.tabIndex = 0; const title = document.createElement("h3"); title.className = "document-title"; title.textContent = doc.title; const id = document.createElement("div"); id.className = "document-id"; id.textContent = doc.document_id; const preview = document.createElement("pre"); preview.className = "document-preview"; preview.textContent = (doc.preview_lines || []).join("\n"); const full = document.createElement("div"); full.className = "document-full"; full.textContent = doc.content; card.append(title, id, preview, full); container.append(card); });
}
async function loadDocuments() { const result = await Demo.api("/knowledge/documents", { token: tenantSession.adminToken }); renderDocuments(result.documents || []); }
$("file").addEventListener("change", async (event) => { selectedFiles = Array.from(event.target.files || []); if (!selectedFiles[0]) return; $("content").value = await selectedFiles[0].text(); $("title").value = fileStem(selectedFiles[0].name); $("document-id").value = documentIdFor(selectedFiles[0].name); });
$("knowledge-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true;
  try { const uploads = selectedFiles.length > 1 ? await Promise.all(selectedFiles.map(async (file, index) => ({ documentId: documentIdFor(file.name, index), title: fileStem(file.name), content: await file.text() }))) : [{ documentId: $("document-id").value.trim(), title: $("title").value.trim(), content: $("content").value }]; let chunks = 0;
    for (const upload of uploads) { const result = await Demo.api("/knowledge/documents", { method: "POST", token: tenantSession.adminToken, body: JSON.stringify({ document_id: upload.documentId, title: upload.title, source: `ui://tenant-upload/${encodeURIComponent(upload.documentId)}`, content: upload.content, version: 1 }) }); chunks += result.chunks; }
    await loadDocuments(); Demo.setStatus($("knowledge-status"), `已导入 ${uploads.length} 篇文档，共 ${chunks} 个知识分块。`); selectedFiles = []; $("file").value = "";
  } catch (error) { Demo.setStatus($("knowledge-status"), error.message, true); } finally { button.disabled = false; }
});

async function loadCustomers() {
  const list = $("customer-list");
  list.innerHTML = '<div class="empty compact">正在读取当前租户客户…</div>';
  Demo.setStatus($("customer-status"), "");
  try {
    const data = await Demo.api("/admin/users", { token: tenantSession.adminToken });
    const customers = (data.users || []).filter((item) => item.role === "user");
    list.replaceChildren();
    if (!customers.length) { list.innerHTML = '<div class="empty compact">当前租户暂无客户。</div>'; return; }
    customers.forEach((customer, index) => {
      const card = document.createElement("article"); card.className = "feature-card customer-card";
      const avatar = document.createElement("div"); avatar.className = "customer-avatar"; avatar.textContent = customer.full_name?.slice(0, 1) || "客";
      const name = document.createElement("h3"); name.textContent = customer.full_name || `客户 ${index + 1}`;
      const email = document.createElement("p"); email.textContent = customer.email || "邮箱已脱敏";
      const state = document.createElement("span"); state.className = "badge"; state.textContent = "模拟客户 · 已启用";
      card.append(avatar, name, email, state); list.append(card);
    });
    Demo.setStatus($("customer-status"), `当前租户共有 ${customers.length} 位演示客户。`);
  } catch (error) {
    list.innerHTML = '<div class="empty compact">客户列表读取失败，请重新登录后再试。</div>';
    Demo.setStatus($("customer-status"), error.message, true);
  }
}

async function loadOperations() {
  try { const [usersData, auditData, deadData] = await Promise.all([Demo.api("/admin/users", { token: tenantSession.adminToken }), Demo.api("/admin/audit-logs?limit=20", { token: tenantSession.adminToken }), Demo.api("/admin/dead-letters?limit=20", { token: tenantSession.adminToken })]); const users = usersData.users || [], logs = auditData.audit_logs || [], dead = deadData.dead_letters || []; const primaryAdmin = users.find((item) => item.role === "admin"); $("user-count").textContent = users.length; $("audit-count").textContent = logs.length; $("dead-count").textContent = dead.filter((item) => item.status !== "replayed").length; const list = $("operations-list"); list.replaceChildren(); const summary = document.createElement("article"); summary.className = "data-item"; const body = document.createElement("div"); const title = document.createElement("strong"); title.textContent = "租户运行正常"; const detail = document.createElement("p"); detail.textContent = `管理员 ${primaryAdmin?.full_name || "1 名"}，近期 ${logs.length} 条审计记录，${dead.length ? ` ${dead.length} 条死信` : "无死信"}。`; body.append(title, detail); summary.append(body); list.append(summary); Demo.setStatus($("operations-status"), "运营数据已刷新。"); } catch (error) { Demo.setStatus($("operations-status"), error.message, true); }
}
async function loadUsage() {
  if (!tenantSession) return;
  try { const data = await Demo.api("/admin/llm-usage", { token: tenantSession.adminToken }); const box = $("usage-list"); box.replaceChildren(); if (!(data.conversations || []).length) { box.innerHTML = '<div class="empty compact">暂无 LLM 调用记录。</div>'; return; } data.conversations.forEach((item) => { const row = document.createElement("article"); row.className = "data-item"; const body = document.createElement("div"); const title = document.createElement("strong"); title.textContent = `会话 ${item.conversation_id.slice(0, 8)}`; const detail = document.createElement("p"); detail.textContent = `${item.llm_calls} 次调用 · 输入 ${item.prompt_tokens} · 输出 ${item.completion_tokens} · 合计 ${item.total_tokens} tokens · $${Number(item.cost_usd).toFixed(6)}`; body.append(title, detail); row.append(body); box.append(row); }); } catch (error) { Demo.setStatus($("usage-status"), error.message, true); }
}
$("refresh-operations").addEventListener("click", loadOperations);
$("refresh-usage").addEventListener("click", loadUsage);
$("refresh-customers").addEventListener("click", loadCustomers);

$("admin-create-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true;
  try {
    const result = await Demo.api("/admin/users", { method: "POST", token: tenantSession.adminToken, body: JSON.stringify({ full_name: $("new-admin-name").value.trim(), email: $("new-admin-email").value.trim(), initial_password: $("new-admin-password").value }) });
    const card = $("admin-login-card"); card.replaceChildren(); card.hidden = false;
    const title = document.createElement("h3"); title.textContent = "管理员创建成功";
    const lines = document.createElement("pre"); lines.className = "document-preview"; lines.textContent = `租户 ID：${result.login.tenant_id}\n登录邮箱：${result.login.email}\n初始密码：${result.login.password}`;
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "button secondary"; copy.textContent = "复制登录信息";
    copy.addEventListener("click", async () => { await navigator.clipboard.writeText(lines.textContent); Demo.setStatus($("admin-create-status"), "登录信息已复制。请通过安全渠道交给新管理员。"); });
    card.append(title, lines, copy); Demo.setStatus($("admin-create-status"), "管理员账号已创建。初始密码仅显示在当前页面。");
    if (tenantSession.features.includes("operations")) await loadOperations();
  } catch (error) { Demo.setStatus($("admin-create-status"), error.message, true); } finally { button.disabled = false; }
});

const tenantActionDock = document.createElement("div"); tenantActionDock.className = "suggestion-list"; tenantActionDock.hidden = true;
$("messages").before(tenantActionDock);
const tenantActionRules = [
  { re: /课表|上课时间/, feature: "learning", label: "查看课表", run: () => Demo.api("/platform/course-schedule", { token: tenantSession.token }) },
  { re: /学习报告|成绩/, feature: "learning", label: "查看学习报告", run: () => Demo.api("/platform/study-report", { token: tenantSession.token }) },
  { re: /订单|账单|发票|退款|退费|余额/, feature: "finance", label: "查询财务信息", run: () => Demo.api("/finance/order", { token: tenantSession.token }) },
  { re: /续费|扣款|请假|缺席|补课|调课/, feature: "services", label: "打开办事功能", run: async () => ({ message: "已识别为办事服务意图；正式客户页面可执行续费、请假、补课或调课操作。" }) },
  { re: /提醒|通知/, feature: "reminders", label: "查看提醒功能", run: async () => ({ message: "提醒演示已启用；真实定时投递不在本次演示范围内。" }) },
  { re: /人工|客服|老师回复/, feature: "handoff", label: "转人工客服", run: async () => { const result = await Demo.api("/handoffs", { method: "POST", token: tenantSession.token, body: JSON.stringify({ conversation_id: tenantSession.conversationId, summary: "客户请求人工协助", reason: "explicit_request", attempted_actions: ["rag_assistant"] }) }); tenantSession.humanMode = true; await loadTenantHandoff(); return { message: `已转人工，工单 ${result.id.slice(0, 8)}。` }; } },
];
function renderTenantActions(text) {
  tenantActionDock.replaceChildren();
  tenantActionRules.filter((item) => tenantSession?.features.includes(item.feature) && item.re.test(text)).forEach((item) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "suggestion-chip"; button.textContent = `立即${item.label}`;
    button.addEventListener("click", async () => { try { const result = await item.run(); Demo.setStatus($("chat-status"), result.message || `${item.label}成功：${JSON.stringify(result.data || result)}`); } catch (error) { Demo.setStatus($("chat-status"), error.message, true); } });
    tenantActionDock.append(button);
  });
  tenantActionDock.hidden = !tenantActionDock.children.length;
}
$("chat-input").addEventListener("input", (event) => renderTenantActions(event.target.value));

const tenantHandoffLabels = { pending: "待处理", in_progress: "处理中", awaiting_confirmation: "待确认结束", ended: "已结束" };
async function loadTenantHandoff() {
  if (!tenantSession?.features.includes("handoff")) return;
  try {
    const data = await Demo.api(`/handoffs/active?conversation_id=${tenantSession.conversationId}`, { token: tenantSession.token });
    tenantActiveHandoff = data.handoff;
    const state = $("tenant-handoff-state"); const actions = $("tenant-handoff-close-actions"); const description = $("tenant-handoff-description");
    if (!tenantActiveHandoff) {
      tenantSession.humanMode = false; state.textContent = "未转人工或已结束"; state.className = "state-badge off"; actions.hidden = true; description.textContent = "AI 正常回复中。"; return;
    }
    state.textContent = tenantHandoffLabels[tenantActiveHandoff.status] || tenantActiveHandoff.status; state.className = `state-badge handoff-${tenantActiveHandoff.status}`;
    tenantSession.humanMode = ["pending", "in_progress", "awaiting_confirmation"].includes(tenantActiveHandoff.status);
    actions.hidden = tenantActiveHandoff.status !== "awaiting_confirmation";
    description.textContent = tenantActiveHandoff.status === "awaiting_confirmation" ? "人工客服申请结束，请在 10 分钟内确认。" : "人工服务期间消息不会交给 AI。";
    await Demo.loadMessages(tenantSession, $("messages"));
  } catch (error) { Demo.setStatus($("chat-status"), error.message, true); }
}
async function respondToTenantHandoff(confirm) {
  if (!tenantActiveHandoff) return;
  try {
    tenantActiveHandoff = await Demo.api(`/handoffs/${tenantActiveHandoff.id}/close-response`, { method: "POST", token: tenantSession.token, body: JSON.stringify({ confirm }) });
    if (confirm) { tenantActiveHandoff = null; tenantSession.humanMode = false; }
    Demo.setStatus($("chat-status"), confirm ? "人工会话已结束，AI 客服已恢复。" : "已通知人工客服继续处理。");
    await loadTenantHandoff();
  } catch (error) { Demo.setStatus($("chat-status"), error.message, true); }
}
$("tenant-confirm-handoff-close").addEventListener("click", () => respondToTenantHandoff(true));
$("tenant-continue-handoff").addEventListener("click", () => respondToTenantHandoff(false));

Demo.bindComposer($("chat-form"), $("chat-input"), $("chat-status"), () => tenantSession, $("messages"));
setInterval(loadTenantHandoff, 2000);

const managedMode = new URLSearchParams(window.location.search).get("managed") === "1";
const sessionKey = managedMode ? "platformTenantSession" : "tenantWorkbenchSession";
const initialSession = JSON.parse(sessionStorage.getItem(sessionKey) || "null");
if (managedMode) {
  $("workspace-back").href = "/ui/platform.html";
  $("workspace-back").textContent = "返回演示租户列表";
  const customerViewButton = document.createElement("button"); customerViewButton.className = "button secondary"; customerViewButton.textContent = "进入客户视角"; customerViewButton.type = "button";
  customerViewButton.addEventListener("click", () => { window.location.href = "/ui/customer.html?managed=1"; });
  document.querySelector(".welcome-strip").append(customerViewButton);
}
if (initialSession) {
  enterWorkbench(initialSession).catch((error) => {
    $("workspace-identity").textContent = error.message;
    $("workspace-identity").className = "status error";
  });
} else {
  window.location.replace(managedMode ? "/ui/platform.html" : "/ui/tenant-auth.html?mode=login");
}
