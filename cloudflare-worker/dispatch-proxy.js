/**
 * Proxy Cloudflare Worker — déclenche envoi.yml sur GitHub Actions.
 *
 * La page HTML appelle ce Worker (pas l'API GitHub directement) :
 * le token GitHub est stocké comme secret Cloudflare, jamais dans le HTML.
 *
 * Secret requis : GITHUB_TOKEN  (fine-grained PAT, permission Actions → Read & write
 *                                sur maxtaillebois/veille-presse-interne-CC)
 */

const ALLOWED_ORIGIN = 'https://maxtaillebois.github.io';
const GITHUB_DISPATCH_URL =
  'https://api.github.com/repos/maxtaillebois/veille-presse-interne-CC/actions/workflows/envoi.yml/dispatches';

const CORS = {
  'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default {
  async fetch(request, env) {
    // Preflight CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405, headers: CORS });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response('Bad Request', { status: 400, headers: CORS });
    }

    const gh = await fetch(GITHUB_DISPATCH_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    // GitHub renvoie 204 No Content en cas de succès
    const text = gh.status === 204 ? '' : await gh.text();
    return new Response(text || null, {
      status: gh.status,
      headers: { ...CORS, 'Content-Type': 'application/json' },
    });
  },
};
