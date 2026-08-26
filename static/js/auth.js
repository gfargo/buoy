/**
 * Authentication for protected same-origin API actions.
 * Credentials live only in this module's memory and are never persisted.
 */

const AUTH_TYPES = new Set(['token', 'basic']);

function normalizedConfig(config) {
  if (!config?.enabled || !AUTH_TYPES.has(config.type)) {
    return { enabled: false, type: null };
  }
  return { enabled: true, type: config.type };
}

function normalizedCredentials(type, value) {
  if (type === 'token' && typeof value?.token === 'string' && value.token) {
    return { token: value.token };
  }
  if (
    type === 'basic'
    && typeof value?.username === 'string'
    && value.username
    && !value.username.includes(':')
    && typeof value?.password === 'string'
    && value.password
  ) {
    return { username: value.username, password: value.password };
  }
  return null;
}

export function utf8Base64(value, encode = btoa) {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return encode(binary);
}

export function authorizationValue(type, credentials) {
  if (type === 'token') return `Bearer ${credentials.token}`;
  if (type === 'basic') {
    return `Basic ${utf8Base64(`${credentials.username}:${credentials.password}`)}`;
  }
  return null;
}

function locationHref() {
  return typeof location === 'undefined' ? null : location.href;
}

function isSameOrigin(input, href) {
  if (!href) return false;
  const candidate = typeof Request !== 'undefined' && input instanceof Request ? input.url : input;
  try {
    return new URL(candidate, href).origin === new URL(href).origin;
  } catch (_) {
    return false;
  }
}

function withAuthorization(input, options, value) {
  const requestHeaders = typeof Request !== 'undefined' && input instanceof Request
    ? input.headers
    : undefined;
  const headers = new Headers(requestHeaders);
  new Headers(options?.headers).forEach((headerValue, name) => headers.set(name, headerValue));
  headers.set('Authorization', value);
  return { ...(options || {}), headers };
}

function showCredentialDialog(type) {
  if (typeof document === 'undefined') return Promise.resolve(null);

  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'auth-dialog';
  dialog.setAttribute('aria-labelledby', 'auth-dialog-title');
  dialog.setAttribute('aria-describedby', 'auth-dialog-description');

  const modeFields = type === 'basic'
    ? `<label class="auth-field">Username<input name="username" type="text" autocomplete="off" required></label>
       <label class="auth-field">Password<input name="password" type="password" autocomplete="off" required></label>`
    : '<label class="auth-field">Access token<input name="token" type="password" autocomplete="off" required></label>';

  dialog.innerHTML = `
    <form class="auth-form" method="dialog">
      <div class="auth-kicker">Protected action</div>
      <h2 id="auth-dialog-title">${type === 'basic' ? 'Sign in to Buoy' : 'Enter access token'}</h2>
      <p id="auth-dialog-description">Credentials are kept in memory for this page only.</p>
      ${modeFields}
      <p class="auth-error" role="alert" hidden></p>
      <div class="auth-actions">
        <button class="auth-cancel" type="button">Cancel</button>
        <button class="auth-submit" type="submit">Continue</button>
      </div>
    </form>`;

  document.body.appendChild(dialog);
  const form = dialog.querySelector('form');
  const error = dialog.querySelector('.auth-error');

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      dialog.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(value);
    };

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const data = new FormData(form);
      const value = type === 'basic'
        ? { username: data.get('username'), password: data.get('password') }
        : { token: data.get('token') };
      const credentials = normalizedCredentials(type, value);
      if (!credentials) {
        error.textContent = type === 'basic' && String(data.get('username')).includes(':')
          ? 'Username cannot contain a colon.'
          : 'Enter valid credentials.';
        error.hidden = false;
        form.querySelector('input')?.focus();
        return;
      }
      finish(credentials);
    });
    dialog.querySelector('.auth-cancel').addEventListener('click', () => finish(null));
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      finish(null);
    });

    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    form.querySelector('input')?.focus();
  });
}

export function createAuthClient({
  fetchImpl = fetch,
  promptCredentials = showCredentialDialog,
  getLocationHref = locationHref,
} = {}) {
  let auth = { enabled: false, type: null };
  let credentials = null;
  let promptInFlight = null;

  function init(config) {
    auth = normalizedConfig(config);
    credentials = null;
  }

  async function requestCredentials() {
    if (promptInFlight) return promptInFlight;
    const type = auth.type;
    const pending = Promise.resolve()
      .then(() => promptCredentials(type))
      .then((value) => {
        const next = normalizedCredentials(type, value);
        if (auth.enabled && auth.type === type) credentials = next;
        return next;
      })
      .finally(() => {
        if (promptInFlight === pending) promptInFlight = null;
      });
    promptInFlight = pending;
    return pending;
  }

  async function authenticatedFetch(input, options) {
    if (!auth.enabled || !isSameOrigin(input, getLocationHref())) {
      return fetchImpl(input, options);
    }

    const firstCredentials = credentials;
    const firstOptions = firstCredentials
      ? withAuthorization(input, options, authorizationValue(auth.type, firstCredentials))
      : options;
    const response = await fetchImpl(input, firstOptions);
    if (response.status !== 401) return response;

    if (credentials === firstCredentials) credentials = null;
    const retryCredentials = credentials || await requestCredentials();
    if (!retryCredentials) return response;

    const retry = await fetchImpl(
      input,
      withAuthorization(input, options, authorizationValue(auth.type, retryCredentials)),
    );
    if (retry.status === 401 && credentials === retryCredentials) credentials = null;
    return retry;
  }

  return { init, fetch: authenticatedFetch };
}

const authClient = createAuthClient();

export function initAuth(config) {
  authClient.init(config);
}

export function authedFetch(input, options) {
  return authClient.fetch(input, options);
}
