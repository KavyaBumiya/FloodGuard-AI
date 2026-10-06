/**
 * FLOODGUARD AI - stable API edge proxy.
 *
 * Why this exists
 * ---------------
 * The Vercel frontend talks to the API over a single build-time variable,
 * NEXT_PUBLIC_API_BASE. That value is inlined when Next.js builds, so it
 * cannot change until a redeploy. The API is published by a Cloudflare
 * *quick* tunnel, which hands out a random hostname on every restart.
 *
 * So: restart the tunnel -> new random URL -> the deployed frontend is
 * pointing at a hostname that no longer resolves. That is the "backend
 * offline" failure, and it recurs on every restart.
 *
 * This Worker sits in between and gives the frontend one permanent
 * *.workers.dev hostname. The random tunnel URL lives in KV instead, and
 * KV can be rewritten with a single command - no Worker redeploy, no
 * Vercel redeploy, no downtime.
 *
 *   Vercel (fixed)  ->  Worker (fixed)  ->  KV origin (changes freely)
 *                    ->  quick tunnel    ->  FastAPI :8000
 */

const ORIGIN_KV_KEY = 'ORIGIN_URL';

// Headers that describe the hop we are replacing, not the request itself.
const STRIPPED_REQUEST_HEADERS = new Set([
  'host',
  'cf-connecting-ip',
  'cf-ipcountry',
  'cf-ray',
  'cf-visitor',
  'x-forwarded-proto',
  'content-length',
]);

// Allow any *.vercel.app preview subdomain plus local dev. Keeping the
// regex here means CORS survives Vercel preview deployments, which get a
// random subdomain on every push and would otherwise fail preflight.
const ALLOWED_ORIGIN = /^(https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|https:\/\/[\w.-]+\.vercel\.app)$/;

function corsHeaders(request) {
  const origin = request.headers.get('Origin');
  const allow = origin && ALLOWED_ORIGIN.test(origin) ? origin : '*';
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': request.headers.get('Access-Control-Request-Headers') || '*',
    'Access-Control-Max-Age': '86400',
  };
}

/**
 * Read the current tunnel URL. KV is the source of truth because it changes
 * without a deploy; ORIGIN_URL in the Worker config is only a bootstrap
 * fallback for the very first request before KV has been seeded.
 */
async function resolveOrigin(env) {
  const cached = await env.ORIGIN.get(ORIGIN_KV_KEY, 'text');
  const raw = (cached || env.ORIGIN_URL || '').trim();
  if (!raw) return null;
  return raw.replace(/\/+$/, '');
}

function jsonResponse(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      ...extra,
    },
  });
}

export default {
  async fetch(request, env) {
    // Preflight never reaches the origin; answer it here so a browser can
    // complete the handshake even while the tunnel is down.
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    const origin = await resolveOrigin(env);
    if (!origin) {
      return jsonResponse(
        503,
        {
          error: 'proxy_unconfigured',
          detail:
            'No origin URL configured. Run: wrangler kv key put --binding=ORIGIN ORIGIN_URL "<tunnel-url>"',
        },
        corsHeaders(request),
      );
    }

    const incoming = new URL(request.url);
    const target = `${origin}${incoming.pathname}${incoming.search}`;

    const headers = new Headers();
    for (const [key, value] of request.headers.entries()) {
      if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
    }
    headers.set('X-Forwarded-Proto', 'https');

    const hasBody = !['GET', 'HEAD'].includes(request.method);

    try {
      // Passing the Request straight through lets the runtime handle the
      // Socket.IO WebSocket upgrade (/socket.io) as well as plain REST.
      const upstream = await fetch(new Request(target, {
        method: request.method,
        headers,
        body: hasBody ? request.body : undefined,
        redirect: 'manual',
      }));

      // CORS is decided here, so drop whatever the origin sent to avoid a
      // duplicate Access-Control-Allow-Origin, which browsers reject.
      const responseHeaders = new Headers(upstream.headers);
      responseHeaders.delete('Access-Control-Allow-Origin');
      responseHeaders.delete('Access-Control-Allow-Credentials');
      for (const [key, value] of Object.entries(corsHeaders(request))) {
        responseHeaders.set(key, value);
      }
      responseHeaders.set('X-Proxy-Origin', origin);

      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    } catch (err) {
      // Usually the tunnel is restarting, or Docker is down.
      return jsonResponse(
        502,
        {
          error: 'origin_unreachable',
          origin,
          detail: `${err.name}: ${err.message}`,
        },
        corsHeaders(request),
      );
    }
  },
};
