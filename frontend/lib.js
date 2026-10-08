// Outils partagés. RÈGLE : tout contenu dynamique (extraits de CV, notes, noms) est inséré en NŒUD TEXTE, jamais en HTML.

// Element.append(null) écrit le mot « null » dans la page : on ignore null/undefined/false pour tous les appels.
const _nativeAppend = Element.prototype.append;
Element.prototype.append = function (...kids) { return _nativeAppend.apply(this, kids.flat(Infinity).filter(k => k != null && k !== false)); };

export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
export const $ = (sel, el = document) => el.querySelector(sel);
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export class ApiError extends Error {
  constructor(status, message, detail) { super(message); this.status = status; this.detail = detail; }
}

export async function api(path, { method = 'GET', body, files } = {}) {
  const headers = { 'X-Requested-With': 'talentlab' };
  let payload;
  if (files) { payload = new FormData(); for (const f of files) payload.append('files', f, f.name); }
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch('/api' + path, { method, headers, body: payload, credentials: 'same-origin' });
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch { /* corps vide */ }
  if (!r.ok) {
    const d = data && data.detail;
    let msg = typeof d === 'string' ? d : (d && d.message) || `Erreur ${r.status}`;
    if (d && Array.isArray(d.errors)) msg += ' — ' + d.errors.join(' ; ');
    if (Array.isArray(d)) msg = d.map(e => e.msg || JSON.stringify(e)).join(' ; ');
    throw new ApiError(r.status, msg, d);
  }
  return data;
}

export function toast(msg, err = false) {
  const t = h('div', { class: 'toast' + (err ? ' err' : ''), role: err ? 'alert' : 'status' }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), err ? 9000 : 4500);
}
export async function guard(fn) {           // exécute une action et affiche l'erreur sans casser l'écran
  try { return await fn(); } catch (e) { toast(e.message || String(e), true); return undefined; }
}

export const LEVELS = {
  confirme_demontre: ['Confirmé et démontré', 'ok'], partiellement_demontre: ['Partiellement démontré', 'warn'],
  declare_sans_preuve: ['Déclaré sans preuve suffisante', 'orange'], non_documente: ['Non documenté', 'grey'], contredit: ['Contredit', 'bad'],
};
export const TIERS = {
  tres_interessant: ['Très intéressant', 'ok'], interessant: ['Intéressant', 'blue'], a_qualifier: ['À qualifier', 'warn'],
  ecart_majeur: ['Écart majeur', 'bad'], adequation_faible: ['Adéquation faible', 'grey'], non_evaluable: ['Non évaluable', 'grey'],
};
export const CATEGORIES = {
  eliminatoire_confirme: 'Éliminatoire confirmé', imperatif: 'Impératif', fortement_differenciant: 'Fortement différenciant',
  souhaitable: 'Souhaitable', contextuel: 'Information contextuelle', a_clarifier: 'À clarifier avec le client',
};
export const SOURCE_KINDS = {
  client_imperatif_confirme: 'Impératif client confirmé', client_precision_validee: 'Précision client validée', retour_entretien: "Retour d'entretien",
  brief_officiel: 'Brief officiel / appel d\'offres', description_initiale: 'Description initiale / historique', note_brief_client: 'Note de brief client',
};
export const STRATEGIES = { exploratory: 'Exploratoire', balanced: 'Équilibrée', strict: 'Stricte' };
export const STATUSES = { a_evaluer: 'À évaluer', a_qualifier: 'À qualifier', qualifie: 'Qualifié', positionne: 'Positionné', ecarte: 'Écarté (décision humaine)' };
export const TRIGGERS = { import: 'Analyse initiale', nouvelle_information: 'Nouvelle information', validation_preuve: 'Validation d\'une information', correction_recruteur: 'Correction du recruteur',
  nouvelle_grille: 'Nouvelle grille', manuel: 'Réévaluation manuelle', ia_assistee: 'Passages désignés par l\'IA' };

export const chip = (text, tone = 'grey') => h('span', { class: `chip ${tone}` }, text);
export const levelChip = lv => { const [t, c] = LEVELS[lv] || [lv, 'grey']; return chip(t, c); };
export const tierChip = t => { const [x, c] = TIERS[t] || [t, 'grey']; return chip(x, c); };
export const fmtDate = s => s ? new Date(s).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '';

export function field(label, control, hint) {
  const id = control.id || 'f' + Math.random().toString(36).slice(2, 8);   // respecte un id déjà fourni (tests, ancres)
  control.id = id;
  return h('div', {}, h('label', { for: id }, label), control, hint ? h('div', { class: 'hint' }, hint) : null);
}
export function bar(value, max = 100, cls = '') {
  const i = h('i'); const b = h('div', { class: 'bar ' + cls, role: 'img', 'aria-label': `${Math.round(value)} sur ${max}` }, i);
  i.style.width = Math.max(0, Math.min(100, (value / max) * 100)) + '%';
  return b;
}
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Requête copiée dans le presse-papiers.'); }
  catch { const ta = h('textarea', {}, text); document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove(); toast('Requête copiée.'); }
}
