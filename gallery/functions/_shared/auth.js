const JWKS_URL = 'https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com';
const EMAIL_RE = /^rkmv\d+@gmail\.com$/i;
let jwksCache;
let jwksExpiry = 0;

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});

export function errorResponse(status, error) {
  return json({ ok: false, error }, status);
}

async function getJwks(fetcher, now) {
  if (jwksCache && now < jwksExpiry) return jwksCache;
  const response = await fetcher(JWKS_URL);
  if (!response.ok) throw new Error('jwks_unavailable');
  const keys = await response.json();
  const age = Number(response.headers.get('cache-control')?.match(/max-age=(\d+)/i)?.[1] ?? 300);
  jwksCache = keys.keys ?? [];
  jwksExpiry = now + Math.min(age, 86400) * 1000;
  return jwksCache;
}

function decodePart(value) {
  return JSON.parse(atob(value.replace(/-/g, '+').replace(/_/g, '/')));
}

export async function verifyToken(token, env, { fetcher = fetch, now = Date.now() } = {}) {
  try {
    if (!env.FIREBASE_PROJECT_ID || typeof token !== 'string') return null;
    const [encodedHeader, encodedPayload, encodedSignature, extra] = token.split('.');
    if (!encodedHeader || !encodedPayload || !encodedSignature || extra !== undefined) return null;
    const header = decodePart(encodedHeader);
    const claims = decodePart(encodedPayload);
    if (header.alg !== 'RS256' || typeof header.kid !== 'string') return null;
    if (claims.aud !== env.FIREBASE_PROJECT_ID || claims.iss !== `https://securetoken.google.com/${env.FIREBASE_PROJECT_ID}`) return null;
    if (!Number.isFinite(claims.exp) || claims.exp <= Math.floor(now / 1000) || typeof claims.sub !== 'string' || !claims.sub || claims.sub.length > 128) return null;
    if (claims.email_verified !== true || typeof claims.email !== 'string' || (!EMAIL_RE.test(claims.email) && !isAdmin(claims.email, env))) return null;
    const jwk = (await getJwks(fetcher, now)).find(key => key.kid === header.kid && key.kty === 'RSA');
    if (!jwk) return null;
    const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
    const data = new TextEncoder().encode(`${encodedHeader}.${encodedPayload}`);
    const signature = Uint8Array.from(atob(encodedSignature.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    if (!await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, signature, data)) return null;
    return { uid: claims.sub, email: claims.email.toLowerCase(), regno: claims.email.match(/^rkmv(\d+)@gmail\.com$/i)?.[1] ?? null };
  } catch {
    return null;
  }
}

export async function authenticate(request, env, options = {}) {
  const token = request.headers.get('authorization')?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return { response: errorResponse(401, 'unauthorized') };
  const user = await verifyToken(token, env, options);
  return user ? { user, token } : { response: errorResponse(401, 'invalid_token') };
}

export async function profileFor(user, token, env, fetcher = fetch) {
  const url = `https://firestore.googleapis.com/v1/projects/${encodeURIComponent(env.FIREBASE_PROJECT_ID)}/databases/(default)/documents/users/${encodeURIComponent(user.uid)}`;
  const response = await fetcher(url, { headers: { authorization: `Bearer ${token}` } });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('profile_unavailable');
  const fields = (await response.json()).fields ?? {};
  return {
    email: fields.email?.stringValue ?? user.email,
    regno: fields.regno?.stringValue ?? user.regno,
    status: fields.status?.stringValue ?? 'pending',
  };
}

export async function requireApproved(request, env, options = {}) {
  const auth = await authenticate(request, env, options);
  if (auth.response) return auth;
  if (isAdmin(auth.user.email, env)) return { ...auth, profile: { email: auth.user.email, status: 'approved', admin: true } };
  try {
    const profile = await profileFor(auth.user, auth.token, env, options.fetcher);
    if (!profile || profile.status !== 'approved') return { response: errorResponse(403, 'not_approved') };
    return { ...auth, profile };
  } catch {
    return { response: errorResponse(401, 'profile_unavailable') };
  }
}

export function isAdmin(email, env) {
  return (env.ADMIN_EMAILS ?? '').split(',').map(value => value.trim().toLowerCase()).filter(Boolean).includes((email ?? '').toLowerCase());
}

export async function requireAdmin(request, env, options = {}) {
  const auth = await authenticate(request, env, options);
  if (auth.response) return auth;
  if (!isAdmin(auth.user.email, env)) return { response: errorResponse(403, 'admin_required') };
  return auth;
}

export function jsonResponse(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers } });
}

export function resetJwksCache() { jwksCache = undefined; jwksExpiry = 0; }
