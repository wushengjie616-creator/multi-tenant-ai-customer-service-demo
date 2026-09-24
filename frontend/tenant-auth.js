const $ = (id) => document.getElementById(id);
const tenantSeed = Date.now().toString().slice(-6);
$('tenant-name-input').value = `我的教育机构-${tenantSeed}`;
$('admin-email-input').value = `admin-${tenantSeed}@demo.example.com`;

function chosenFeatures() {
  return Array.from(document.querySelectorAll('.feature-option input:checked')).map((node) => node.value);
}
function showMode(mode) {
  const register = mode === 'register';
  $('create-tenant-form').hidden = !register;
  $('tenant-login-form').hidden = register;
  $('show-register').className = `button ${register ? '' : 'secondary'}`.trim();
  $('show-login').className = `button ${register ? 'secondary' : ''}`.trim();
}
function continueToWorkbench(data) {
  sessionStorage.setItem('tenantWorkbenchSession', JSON.stringify(data));
  window.location.href = '/ui/tenant.html';
}

$('show-register').addEventListener('click', () => showMode('register'));
$('show-login').addEventListener('click', () => showMode('login'));
$('select-all-features').addEventListener('click', () => {
  const boxes = Array.from(document.querySelectorAll('.feature-option input'));
  const shouldCheck = boxes.some((box) => !box.checked);
  boxes.forEach((box) => { box.checked = shouldCheck; });
});

$('create-tenant-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const features = chosenFeatures();
  if (!features.length) return Demo.setStatus($('create-status'), '请至少选择一个功能。', true);
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true; Demo.setStatus($('create-status'), '正在创建独立租户…');
  try {
    const data = await Demo.api('/demo/tenants', { method: 'POST', body: JSON.stringify({ tenant_name: $('tenant-name-input').value.trim(), admin_name: $('admin-name-input').value.trim(), admin_email: $('admin-email-input').value.trim(), admin_password: $('admin-password-input').value, features }) });
    continueToWorkbench(data);
  } catch (error) { Demo.setStatus($('create-status'), error.message, true); } finally { button.disabled = false; }
});

$('tenant-login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true;
  try {
    const login = await Demo.api('/auth/login', { method: 'POST', body: JSON.stringify({ tenant_id: $('login-tenant-id').value.trim(), email: $('login-email').value.trim(), password: $('login-password').value }) });
    const data = await Demo.api('/demo/workbench-session', { method: 'POST', token: login.access_token });
    continueToWorkbench(data);
  } catch (error) { Demo.setStatus($('login-status'), error.message, true); } finally { button.disabled = false; }
});

showMode(new URLSearchParams(window.location.search).get('mode') === 'login' ? 'login' : 'register');
