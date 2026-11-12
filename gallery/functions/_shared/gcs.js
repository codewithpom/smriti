// Private Google Cloud Storage adapter for Workers. Service-account credentials
// stay in the Pages secret; callers only see the common R2-shaped store API.
const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const STORAGE_API = 'https://storage.googleapis.com';
const STORAGE_SCOPE = 'https://www.googleapis.com/auth/devstorage.read_write';
const TOKEN_LIFETIME_SECONDS = 3600;
const tokenCache = new Map();

const base64url = (bytes) => {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
};

function jsonPart(value) {
  return base64url(new TextEncoder().encode(JSON.stringify(value)));
}

function credentials(env) {
  let value;
  try { value = JSON.parse(env.GOOGLE_SERVICE_ACCOUNT_JSON ?? ''); } catch { throw new Error('firebase_storage_not_configured'); }
  if (!value || typeof value.client_email !== 'string' || !value.client_email.includes('@') ||
      typeof value.private_key !== 'string' || !value.private_key.includes('PRIVATE KEY')) {
    throw new Error('firebase_storage_not_configured');
  }
  const bucket = env.FIREBASE_STORAGE_BUCKET;
  if (typeof bucket !== 'string' || !/^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$/.test(bucket)) {
    throw new Error('firebase_storage_not_configured');
  }
  return { clientEmail: value.client_email, privateKey: value.private_key, bucket };
}

function privateKeyBytes(pem) {
  const body = pem.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g, '');
  try { return Uint8Array.from(atob(body), char => char.charCodeAt(0)); }
  catch { throw new Error('firebase_storage_not_configured'); }
}

async function accessToken(creds, fetcher, now) {
  const cached = tokenCache.get(creds.clientEmail);
  if (cached && now < cached.expiresAt) return cached.value;

  let key;
  try {
    key = await crypto.subtle.importKey(
      'pkcs8', privateKeyBytes(creds.privateKey),
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign'],
    );
  } catch { throw new Error('firebase_storage_not_configured'); }
  const issuedAt = Math.floor(now / 1000);
  const head = jsonPart({ alg: 'RS256', typ: 'JWT' });
  const claims = jsonPart({
    iss: creds.clientEmail,
    scope: STORAGE_SCOPE,
    aud: TOKEN_URL,
    iat: issuedAt,
    exp: issuedAt + TOKEN_LIFETIME_SECONDS,
  });
  const unsigned = `${head}.${claims}`;
  const signature = new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(unsigned)));
  const assertion = `${unsigned}.${base64url(signature)}`;
  let response;
  try {
    response = await fetcher(TOKEN_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        assertion,
      }),
    });
  } catch { throw new Error('firebase_storage_unavailable'); }
  if (!response.ok) throw new Error('firebase_storage_unavailable');
  let result;
  try { result = await response.json(); } catch { throw new Error('firebase_storage_unavailable'); }
  if (typeof result.access_token !== 'string' || !Number.isFinite(Number(result.expires_in))) {
    throw new Error('firebase_storage_unavailable');
  }
  const expiresIn = Math.max(60, Math.min(Number(result.expires_in), TOKEN_LIFETIME_SECONDS));
  tokenCache.set(creds.clientEmail, { value: result.access_token, expiresAt: now + (expiresIn - 60) * 1000 });
  return result.access_token;
}

async function storageRequest(fetcher, creds, now, url, init = {}) {
  const token = await accessToken(creds, fetcher, now());
  let response;
  try {
    response = await fetcher(url, {
      ...init,
      headers: { ...(init.headers ?? {}), authorization: `Bearer ${token}` },
    });
  } catch { throw new Error('firebase_storage_unavailable'); }
  return response;
}

function objectUrl(bucket, key) {
  return `${STORAGE_API}/storage/v1/b/${encodeURIComponent(bucket)}/o/${encodeURIComponent(key)}`;
}

export function firebaseStorage(env, { fetcher = fetch, now = Date.now } = {}) {
  const creds = credentials(env);
  const bucketUrl = `${STORAGE_API}/storage/v1/b/${encodeURIComponent(creds.bucket)}/o`;
  return {
    async get(key) {
      const response = await storageRequest(fetcher, creds, now, `${objectUrl(creds.bucket, key)}?alt=media`);
      if (response.status === 404) return null;
      if (!response.ok) throw new Error('firebase_storage_unavailable');
      const body = await response.arrayBuffer();
      const sizeHeader = response.headers.get('content-length');
      return {
        body,
        httpMetadata: { contentType: response.headers.get('content-type') ?? undefined },
        size: sizeHeader && Number.isFinite(Number(sizeHeader)) ? Number(sizeHeader) : body.byteLength,
      };
    },

    async put(key, body, options = {}) {
      const url = new URL(`${STORAGE_API}/upload/storage/v1/b/${encodeURIComponent(creds.bucket)}/o`);
      url.searchParams.set('uploadType', 'media');
      url.searchParams.set('name', key);
      const response = await storageRequest(fetcher, creds, now, url.toString(), {
        method: 'POST',
        headers: { 'content-type': options.httpMetadata?.contentType ?? 'application/octet-stream' },
        body,
      });
      if (!response.ok) throw new Error('firebase_storage_unavailable');
    },

    async list({ prefix = '', cursor } = {}) {
      const url = new URL(bucketUrl);
      url.searchParams.set('maxResults', '1000');
      if (prefix) url.searchParams.set('prefix', prefix);
      if (cursor) url.searchParams.set('pageToken', cursor);
      const response = await storageRequest(fetcher, creds, now, url.toString());
      if (!response.ok) throw new Error('firebase_storage_unavailable');
      let result;
      try { result = await response.json(); } catch { throw new Error('firebase_storage_unavailable'); }
      const objects = (Array.isArray(result.items) ? result.items : []).map(item => ({
        key: item.name,
        size: Number(item.size) || 0,
      }));
      return {
        objects,
        truncated: typeof result.nextPageToken === 'string' && !!result.nextPageToken,
        cursor: result.nextPageToken,
      };
    },

    async delete(key) {
      const response = await storageRequest(fetcher, creds, now, objectUrl(creds.bucket, key), { method: 'DELETE' });
      if (response.status === 404) return;
      if (!response.ok) throw new Error('firebase_storage_unavailable');
    },
  };
}

export function firebaseStorageConfigured(env) {
  try { credentials(env); return true; } catch { return false; }
}

export function resetFirebaseTokenCache() { tokenCache.clear(); }
