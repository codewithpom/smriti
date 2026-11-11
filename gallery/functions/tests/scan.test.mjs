import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign } from 'node:crypto';
import { resetJwksCache } from '../_shared/auth.js';
import { onRequestPost, onRequestGet } from '../api/admin/scan.js';

const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...publicKey.export({format:'jwk'}),kid:'scan-test'};
function fixture(upstream=()=>{throw new Error('unexpected fetch')}){resetJwksCache();globalThis.fetch=async(url,init)=>url.includes('googleapis.com/service_accounts/')?Response.json({keys:[jwk]}):upstream(url,init);const values=new Map(),env={FIREBASE_PROJECT_ID:'scan-test',ADMIN_EMAILS:'admin@example.com',PHOTOS:{async get(k){return values.has(k)?{body:new TextEncoder().encode(values.get(k))}:null},async put(k,v){values.set(k,String(v))},async list(){return{objects:[]}}}};function request(method='GET',body,role='admin'){const headers={};if(role!=='anonymous'){const enc=x=>Buffer.from(JSON.stringify(x)).toString('base64url'),input=`${enc({alg:'RS256',kid:jwk.kid})}.${enc({aud:env.FIREBASE_PROJECT_ID,iss:`https://securetoken.google.com/${env.FIREBASE_PROJECT_ID}`,sub:role,email:role==='admin'?'admin@example.com':'rkmv1234@gmail.com',email_verified:true,exp:Math.floor(Date.now()/1000)+3600})}`;headers.authorization=`Bearer ${input}.${sign('RSA-SHA256',Buffer.from(input),privateKey).toString('base64url')}`;}return new Request('https://gallery.test/api/admin/scan',{method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})});}return{env,request};}
const noStore=response=>{assert.equal(response.headers.get('cache-control'),'no-store');return response;};

test('scan dispatch sends exact URL, headers and string inputs', async () => {
  const calls = [];
  const { env, request } = fixture((url, init) => { calls.push({ url, ...init }); return new Response(null, { status: 204 }); });
  Object.assign(env, { GITHUB_REPO: 'owner/name', GITHUB_DISPATCH_TOKEN: 'fake-test-token' });
  for (const body of [{ fetchFacebook: true, recluster: false, reembed: true, migrateFrom: 'kv' }, {}]) {
    assert.deepEqual(await noStore(await onRequestPost({ env, request: request('POST', body) })).json(), { ok: true });
    const call = calls.at(-1);
    assert.equal(call.url, 'https://api.github.com/repos/owner/name/actions/workflows/scan.yml/dispatches');
    assert.equal(call.method, 'POST');
    assert.deepEqual(call.headers, { Authorization: 'Bearer fake-test-token', Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'smriti-admin', 'Content-Type': 'application/json' });
    assert.deepEqual(JSON.parse(call.body), { ref: 'automation/scan', inputs: { fetch_facebook: String(!!body.fetchFacebook), recluster: String(!!body.recluster), reembed: String(!!body.reembed), migrate_from: body.migrateFrom ?? '' } });
  }
});
test('scan denies non-admins and missing or invalid configuration', async () => {
  const { env, request } = fixture();
  for (const [handler, method] of [[onRequestPost, 'POST'], [onRequestGet, 'GET']]) {
    for (const [role, status] of [['anonymous', 401], ['student', 403]]) assert.equal(noStore(await handler({ env, request: request(method, method === 'POST' ? {} : undefined, role) })).status, status);
    for (const config of [{}, { GITHUB_DISPATCH_TOKEN: 'fake-token' }, { GITHUB_DISPATCH_TOKEN: 'fake-token', GITHUB_REPO: '../owner/name' }, { GITHUB_DISPATCH_TOKEN: 'fake-token', GITHUB_REPO: 'owner/name\n' }]) {
      const response = noStore(await handler({ env: { ...env, ...config }, request: request(method, method === 'POST' ? {} : undefined) }));
      assert.equal(response.status, 501);
      assert.equal((await response.json()).error, 'not_configured');
    }
  }
});
test('scan maps upstream failures and network errors without details', async () => {
  for (const status of [200, 401, 403, 500, 'throw']) {
    const { env, request } = fixture(() => { if (status === 'throw') throw new Error('private-details'); return new Response('private-upstream', { status }); });
    Object.assign(env, { GITHUB_REPO: 'owner/name', GITHUB_DISPATCH_TOKEN: 'fake-token' });
    for (const [handler, method] of [[onRequestPost, 'POST'], [onRequestGet, 'GET']]) {
      const response = noStore(await handler({ env, request: request(method, method === 'POST' ? {} : undefined) }));
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), { ok: false, error: 'dispatch_failed' });
    }
  }
});
test('scan GET returns latest run fields or none', async () => {
  let runs = [];
  const { env, request } = fixture((url, init) => {
    assert.equal(url, 'https://api.github.com/repos/owner/name/actions/workflows/scan.yml/runs?per_page=1');
    assert.equal(init.method, 'GET');
    return Response.json({ workflow_runs: runs });
  });
  Object.assign(env, { GITHUB_REPO: 'owner/name', GITHUB_DISPATCH_TOKEN: 'fake-token' });
  assert.deepEqual(await noStore(await onRequestGet({ env, request: request() })).json(), { none: true });
  runs = [{ status: 'completed', conclusion: 'success', created_at: '2026-01-01T00:00:00Z', html_url: 'https://github.com/owner/name/actions/runs/1', private: 'hidden' }];
  assert.deepEqual(await noStore(await onRequestGet({ env, request: request() })).json(), { status: 'completed', conclusion: 'success', createdAt: runs[0].created_at, url: runs[0].html_url });
});
test('scan validates request bodies before dispatch', async () => {
  const { env, request } = fixture();
  Object.assign(env, { GITHUB_REPO: 'owner/name', GITHUB_DISPATCH_TOKEN: 'fake-token' });
  for (const body of [null, [], { fetchFacebook: 'true' }, { recluster: 1 }]) assert.equal((await onRequestPost({ env, request: request('POST', body) })).status, 400);
  assert.equal((await onRequestPost({ env, request: request('POST') })).status, 400);
});
