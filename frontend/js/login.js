// Sign-in screen (only shown when the server has APP_PASSWORD set).
let shown = false;

export function showLogin() {
  if (shown) return;
  shown = true;
  const el = document.createElement('div');
  el.className = 'login-screen';
  el.innerHTML = `
    <form class="login-card glass" autocomplete="on">
      <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="currentColor"/>
        <path d="M8.5 11.5v9M12.5 9v14M16.5 13v6M20.5 10v12M24.5 12.5v7" stroke="#0a0b10" stroke-width="2.6" stroke-linecap="round"/></svg>
      <h1>WonderPlay</h1>
      <p class="muted">Sign in to your music</p>
      <input type="text" name="username" value="wonderplay" autocomplete="username" hidden>
      <input class="form-control wp-input" type="password" name="password" placeholder="Password"
             autocomplete="current-password" required aria-label="Password" autofocus>
      <div class="login-error" role="alert" hidden></div>
      <button class="pill-btn pill-btn-primary w-100" type="submit">Sign in</button>
    </form>`;
  document.body.append(el);
  document.body.classList.add('locked');
  const form = el.querySelector('form');
  const err = el.querySelector('.login-error');
  form.password.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button');
    btn.disabled = true;
    err.hidden = true;
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: form.password.value }),
      });
      if (res.ok) { location.reload(); return; }
      const data = await res.json().catch(() => ({}));
      err.textContent = data.detail || 'Sign in failed';
    } catch {
      err.textContent = "Can't reach the server";
    }
    err.hidden = false;
    btn.disabled = false;
    form.password.select();
  });
}

/** Returns true when the app may start (no password, or already signed in, or offline). */
export async function checkAuth() {
  try {
    const res = await fetch('/api/auth/status', { cache: 'no-store' });
    if (!res.ok) return true;
    const st = await res.json();
    if (st.required && !st.authenticated) { showLogin(); return false; }
    document.body.classList.toggle('auth-enabled', !!st.required);
    return true;
  } catch {
    return true;   // offline: let cached data and downloads work
  }
}

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  location.reload();
}
