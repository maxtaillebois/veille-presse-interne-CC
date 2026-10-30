import { PDFDocument } from 'pdf-lib';

const ALLOWED_ORIGIN = 'https://maxtaillebois.github.io';
const GITHUB_DISPATCH_URL =
  'https://api.github.com/repos/maxtaillebois/veille-presse-interne-CC/actions/workflows/envoi.yml/dispatches';

// Aperçu PDF (GET /pdf?name=fichier.pdf&pages=4-6) : le Worker récupère la pièce
// jointe dans la boîte Outlook (mêmes motifs de recherche que
// lance_veille_local.py) et ne renvoie que les pages de l'article. Seuls les
// fichiers présents dans le Google Sheet de la semaine sont servis.
const SHEET_CSV_URL =
  'https://docs.google.com/spreadsheets/d/1t5e1hJ482g-wl6gHeoR0J3yTR-pVOgc9z0rHGREGMN0/gviz/tq?tqx=out:csv&sheet=Veille%20Procivis';
const SUBJECT_PATTERNS = ['retombees', 'retombée'];
const SEARCH_DAYS = 21;
const PREVIEW_CACHE_SECONDS = 3600;

const CORS = {
  'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/pdf') {
      return previewPdf(request, url, env, ctx);
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
        'User-Agent': 'veille-dispatch-worker',
      },
      body: JSON.stringify(body),
    });
    const text = gh.status === 204 ? '' : await gh.text();
    return new Response(text || null, {
      status: gh.status,
      headers: { ...CORS, 'Content-Type': 'application/json' },
    });
  },
};

// ─── Aperçu PDF ────────────────────────────────────────────────────────────

function textError(status, msg) {
  return new Response(msg, {
    status,
    headers: { ...CORS, 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

async function previewPdf(request, url, env, ctx) {
  const name = (url.searchParams.get('name') || '').trim();
  const pages = (url.searchParams.get('pages') || '').trim();
  if (!/^[\w.\- ]+\.pdf$/i.test(name) || (pages && !/^\d+(-\d+)?$/.test(pages))) {
    return textError(400, 'Paramètres invalides.');
  }

  const cache = caches.default;
  const cacheKey = new Request(
    `${url.origin}/pdf?name=${encodeURIComponent(name)}&pages=${encodeURIComponent(pages)}`);
  const cached = await cache.match(cacheKey);
  if (cached) return cached;

  // Garde-fou : uniquement les articles listés dans le Sheet.
  if (!(await isInSheet(name, pages))) {
    return textError(404, "Cet article n'est pas (ou plus) dans le Sheet de la semaine.");
  }

  let bytes;
  try {
    bytes = await fetchAttachment(env, name);
  } catch (e) {
    return textError(502, `Outlook inaccessible : ${e.message}`);
  }
  if (!bytes) return textError(404, `PDF introuvable dans Outlook : ${name}`);

  let out = bytes;
  if (pages) {
    try {
      out = await slicePages(bytes, pages);
    } catch (e) {
      return textError(500, `Découpage impossible (${pages}) : ${e.message}`);
    }
  }

  const resp = new Response(out, {
    headers: {
      ...CORS,
      'Content-Type': 'application/pdf',
      'Content-Disposition': `inline; filename="${name.replace(/\.pdf$/i, '')}${pages ? '_p' + pages : ''}.pdf"`,
      'Cache-Control': `private, max-age=${PREVIEW_CACHE_SECONDS}`,
    },
  });
  const toCache = resp.clone();
  toCache.headers.set('Cache-Control', `public, max-age=${PREVIEW_CACHE_SECONDS}`);
  ctx.waitUntil(cache.put(cacheKey, toCache));
  return resp;
}

async function isInSheet(name, pages) {
  const r = await fetch(SHEET_CSV_URL, { cf: { cacheTtl: 60 } });
  if (!r.ok) return false;
  const rows = parseCsv(await r.text());
  const header = (rows.shift() || []).map(h => h.trim().toLowerCase());
  const iName = header.indexOf('nom fichier pdf');
  const iPages = header.indexOf('pages pdf');
  if (iName < 0) return false;
  return rows.some(row =>
    (row[iName] || '').trim() === name &&
    (!pages || iPages < 0 || (row[iPages] || '').trim() === pages));
}

function parseCsv(text) {
  const rows = [];
  let row = [], field = '', inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') inQuotes = false;
      else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

let graphToken = null;   // { value, expires } — réutilisé tant que l'isolat vit

async function getGraphToken(env) {
  if (graphToken && graphToken.expires > Date.now() + 60000) return graphToken.value;
  const r = await fetch(
    `https://login.microsoftonline.com/${env.OUTLOOK_TENANT}/oauth2/v2.0/token`, {
      method: 'POST',
      body: new URLSearchParams({
        client_id: env.OUTLOOK_CLIENT_ID,
        client_secret: env.OUTLOOK_CLIENT_SECRET,
        scope: 'https://graph.microsoft.com/.default',
        grant_type: 'client_credentials',
      }),
    });
  if (!r.ok) throw new Error(`token HTTP ${r.status}`);
  const j = await r.json();
  graphToken = { value: j.access_token, expires: Date.now() + j.expires_in * 1000 };
  return graphToken.value;
}

async function graphGet(env, path, raw = false) {
  const token = await getGraphToken(env);
  const r = await fetch(`https://graph.microsoft.com/v1.0/users/${env.OUTLOOK_USER}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`Graph HTTP ${r.status}`);
  return raw ? r.arrayBuffer() : r.json();
}

async function fetchAttachment(env, name) {
  const since = new Date(Date.now() - SEARCH_DAYS * 86400000).toISOString();
  const seen = new Set();
  for (const pattern of SUBJECT_PATTERNS) {
    const q = `?$search=${encodeURIComponent(`"${pattern}"`)}&$top=50` +
      '&$select=id,receivedDateTime,hasAttachments';
    const msgs = (await graphGet(env, `/messages${q}`)).value || [];
    for (const m of msgs) {
      if (seen.has(m.id) || !m.hasAttachments || m.receivedDateTime < since) continue;
      seen.add(m.id);
      const atts = (await graphGet(env,
        `/messages/${m.id}/attachments?$select=id,name,contentType`)).value || [];
      const att = atts.find(a => a.name === name);
      if (att) {
        return new Uint8Array(await graphGet(env,
          `/messages/${m.id}/attachments/${att.id}/$value`, true));
      }
    }
  }
  return null;
}

async function slicePages(bytes, pages) {
  const [a, b] = pages.split('-').map(Number);
  const start = a, end = b || a;
  const src = await PDFDocument.load(bytes, { ignoreEncryption: true });
  const total = src.getPageCount();
  if (start < 1 || end < start || start > total) throw new Error(`hors du document (${total} p.)`);
  const indices = [];
  for (let i = start; i <= Math.min(end, total); i++) indices.push(i - 1);
  const out = await PDFDocument.create();
  for (const p of await out.copyPages(src, indices)) out.addPage(p);
  return out.save();
}
