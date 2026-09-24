const $ = (id) => document.getElementById(id);

async function enterTenant(tenantId, customerUserId, button) {
  button.disabled = true;
  try {
    const session = await Demo.api(`/demo/platform/tenants/${tenantId}/session`, {
      method: "POST", body: JSON.stringify({ customer_user_id: customerUserId || null }),
    });
    sessionStorage.setItem("platformTenantSession", JSON.stringify(session));
    window.location.href = "/ui/tenant.html?managed=1";
  } catch (error) { Demo.setStatus($("platform-status"), error.message, true); button.disabled = false; }
}

async function enterCustomer(tenantId, customerUserId, button) {
  button.disabled = true;
  try {
    const session = await Demo.api(`/demo/platform/tenants/${tenantId}/session`, {
      method: "POST", body: JSON.stringify({ customer_user_id: customerUserId || null }),
    });
    sessionStorage.setItem("platformTenantSession", JSON.stringify(session));
    sessionStorage.setItem("tenantCustomerSession", JSON.stringify({
      token: session.customer_access_token,
      tenantId: session.tenant_id,
      tenantName: session.tenant_name,
      customerName: session.customer_name,
      userId: session.customer_user_id,
      conversationId: session.customer_conversation_id,
      features: session.features,
      dependencyMode: session.dependency_mode,
    }));
    window.location.href = "/ui/customer.html?managed=1";
  } catch (error) { Demo.setStatus($("platform-status"), error.message, true); button.disabled = false; }
}

function tenantCard(tenant) {
  const card = document.createElement("article"); card.className = "feature-card";
  const title = document.createElement("h3"); title.textContent = `${tenant.tenant_code} · ${tenant.tenant_name}`;
  const scenario = document.createElement("p"); scenario.textContent = tenant.scenario;
  const stats = document.createElement("p"); stats.textContent = `${tenant.user_count} 个账号 · ${tenant.conversation_count} 个会话`;
  const features = document.createElement("small"); features.textContent = `功能：${tenant.features.join("、") || "基础功能"}`;
  const label = document.createElement("label"); label.textContent = "模拟客户账号";
  const select = document.createElement("select");
  if (!tenant.customers.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "自动创建演示客户"; select.append(option); }
  tenant.customers.forEach((customer) => { const option = document.createElement("option"); option.value = customer.user_id; option.textContent = `${customer.full_name} · ${customer.email}`; select.append(option); });
  label.append(select);
  const actions = document.createElement("div"); actions.className = "card-actions";
  const tenantButton = document.createElement("button"); tenantButton.className = "button"; tenantButton.textContent = "进入租户工作台";
  tenantButton.addEventListener("click", () => enterTenant(tenant.tenant_id, select.value, tenantButton));
  const customerButton = document.createElement("button"); customerButton.className = "button secondary"; customerButton.textContent = "以所选客户进入";
  customerButton.addEventListener("click", () => enterCustomer(tenant.tenant_id, select.value, customerButton));
  actions.append(tenantButton, customerButton);
  card.append(title, scenario, stats, features, label, actions); return card;
}

async function loadTenants() {
  try {
    const data = await Demo.api("/demo/platform/tenants"); const box = $("platform-tenants"); box.replaceChildren();
    $("tenant-count").textContent = data.tenants.length;
    data.tenants.forEach((tenant) => box.append(tenantCard(tenant)));
  } catch (error) { Demo.setStatus($("platform-status"), error.message, true); }
}

$("refresh-platform").addEventListener("click", loadTenants); loadTenants();
