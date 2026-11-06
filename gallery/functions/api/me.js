import { authenticate, profileFor, isAdmin, errorResponse, jsonResponse } from '../_shared/auth.js';

export async function onRequestGet({ request, env }) {
  const auth = await authenticate(request, env);
  if (auth.response) return auth.response;
  try {
    const profile = await profileFor(auth.user, auth.token, env);
    return jsonResponse({ ok: true, email: auth.user.email, regno: profile?.regno ?? auth.user.regno, status: profile?.status ?? 'pending', admin: isAdmin(auth.user.email, env) });
  } catch {
    return errorResponse(401, 'profile_unavailable');
  }
}
