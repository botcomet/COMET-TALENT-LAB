import { $, api, ApiError, clear, copyText, field, guard, h, toast, fmtDate, chip } from './lib.js';
import { renderMission, stopPolling } from './mission.js';
import { renderAssistant } from './assistant.js';

const state = { user: null, config: null };
export { state };

async function boot() {
  try {
    state.user = await api('/auth/me');
    state.config = await api('/config');
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return renderLogin();
    toast(e.message, true);
    return renderLogin();
  }
  shell();
  window.addEventListener('hashchange', route);
  route();
}

async function renderLogin() {
  clear($('#topbar')).append(h('span', { class: 'brand' }, 'Comet', h('b', {}, 'Talent Lab')));
  const main = clear($('#main'));
  let users = null;
  try { users = await api('/auth/dev-users'); } catch { /* mode passerelle */ }
  if (!users) {
    main.append(h('div', { class: 'card login' }, h('h1', {}, 'Authentification requise'),
      h('p', {}, "Cette instance s'appuie sur l'authentification Comet (passerelle). Ouvrez l'application depuis l'annuaire Constellation."),
      h('p', { class: 'hint' }, "Aucune connexion locale n'est proposée en dehors du mode développement.")));
    return;
  }
  const sel = h('select', { id: 'login-user', 'aria-label': 'Compte' }, users.map(u => h('option', { value: u.email }, `${u.display_name} — ${u.role}`)));
  main.append(h('div', { class: 'card login' },
    h('div', { class: 'banner' }, 'Mode développement — comptes FICTIFS, aucune donnée réelle'),
    h('h1', {}, 'Connexion'), field('Compte de démonstration', sel),
    h('p', { class: 'hint' }, 'En production : authentification Comet via la passerelle (SSO), jamais de connexion locale.'),
    h('div', { class: 'row end' }, h('button', { class: 'primary', id: 'login-btn', onclick: () => guard(async () => { await api('/auth/dev-login', { method: 'POST', body: { email: sel.value } }); boot(); }) }, 'Se connecter'))));
}

function shell() {
  const top = clear($('#topbar'));
  top.append(
    h('a', { class: 'brand', href: '#/' }, 'Comet', h('b', {}, 'Talent Lab')),
    h('nav', { 'aria-label': 'Navigation principale' },
      h('a', { href: '#/missions' }, 'Mes missions'), h('a', { href: '#/shared' }, 'Missions partagées'), h('a', { href: '#/library' }, 'Bibliothèque Talent'), h('a', { href: '#/coach' }, 'Talent Coach')),
    h('span', { class: 'spacer' }),
    h('span', { id: 'whoami' }, state.user.display_name),
    h('button', { class: 'small', onclick: () => guard(async () => { await api('/auth/logout', { method: 'POST' }); location.hash = ''; location.reload(); }) }, 'Déconnexion'));
  if (state.user.auth_mode === 'dev') document.body.prepend(h('div', { class: 'banner', id: 'devbanner' }, 'Mode développement — comptes fictifs, données de démonstration uniquement'));
}

function setCurrent() {
  const hash = location.hash || '#/';
  document.querySelectorAll('#topbar nav a').forEach(a => a.getAttribute('href') === hash.split('/').slice(0, 2).join('/') ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
}

async function route() {
  stopPolling();
  setCurrent();
  const parts = (location.hash || '#/').replace(/^#\/?/, '').split('/').filter(Boolean);
  const main = clear($('#main'));
  $('#assistant').hidden = true;
  const view = parts[0] || 'home';
  await guard(async () => {
    if (view === 'home') return home(main);
    if (view === 'missions') return missionList(main, 'mine');
    if (view === 'shared') return missionList(main, 'shared');
    if (view === 'new') return newMission(main);
    if (view === 'library') return library(main);
    if (view === 'coach') return coach(main);
    if (view === 'mission' && parts[1]) return renderMission(main, parts[1], parts[2] || 'needs', parts[3]);
    main.append(h('p', {}, 'Page introuvable.'));
  });
  main.focus({ preventScroll: true });
}

function home(main) {
  const tile = (title, text, href) => h('button', { class: 'tile', onclick: () => { location.hash = href; } }, h('h2', {}, title), h('p', {}, text));
  main.append(
    h('h1', {}, 'COMET Talent Lab'),
    h('p', { class: 'muted' }, 'Copilote métier du Talent Management : comprendre le besoin, chercher, comparer et qualifier — avec preuves, sans boîte noire.'),
    h('div', { class: 'grid cols-4' },
      tile('Nouvelle mission', 'Coller le brief, ajouter les précisions, valider les critères.', '#/new'),
      tile('Mes missions', 'Reprendre vos missions : recherches, CV, qualifications.', '#/missions'),
      tile('Missions partagées', 'Missions ou stratégies de sourcing partagées par un collègue.', '#/shared'),
      tile('Bibliothèque Talent', 'Enseignements, méthodes de recherche et erreurs de matching.', '#/library')),
    h('div', { class: 'alert blue' }, 'Principes : le besoin client est la référence · une mention n’est pas une démonstration · un impératif non satisfait n’est jamais compensé · aucune décision automatique de rejet.'));
}

async function missionList(main, scope) {
  const list = await api(`/missions?scope=${scope}`);
  main.append(h('div', { class: 'row' }, h('h1', { class: 'grow' }, scope === 'mine' ? 'Mes missions' : 'Missions partagées'), scope === 'mine' ? h('button', { class: 'primary', onclick: () => { location.hash = '#/new'; } }, 'Nouvelle mission') : null));
  if (!list.length) return main.append(h('p', { class: 'muted' }, scope === 'mine' ? 'Aucune mission pour le moment.' : 'Aucune mission partagée avec vous.'));
  main.append(h('div', { class: 'card table-wrap' }, h('table', {},
    h('thead', {}, h('tr', {}, ['Mission', 'Client', 'Propriétaire', 'Accès', 'Grille', 'Mise à jour'].map(t => h('th', {}, t)))),
    h('tbody', {}, list.map(m => h('tr', { class: 'clickable', tabindex: 0, onclick: () => { location.hash = `#/mission/${m.id}/needs`; }, onkeydown: e => { if (e.key === 'Enter') location.hash = `#/mission/${m.id}/needs`; } },
      h('td', {}, h('b', {}, m.title)), h('td', {}, m.client), h('td', {}, m.owner), h('td', {}, chip(m.access === 'owner' ? 'Propriétaire' : m.access, 'blue')),
      h('td', {}, m.grid_version ? `v${m.grid_version}` : '—'), h('td', {}, fmtDate(m.updated_at))))))));
}

function newMission(main) {
  const client = h('input', { type: 'text', maxlength: 200, placeholder: 'Client (nom interne ou code)' });
  const title = h('input', { type: 'text', maxlength: 300, placeholder: 'Intitulé de la mission', style: undefined });
  const brief = h('textarea', { maxlength: 60000, rows: 14, placeholder: "Coller le descriptif client : contexte, missions, compétences requises / souhaitées, modalités…" });
  const file = h('input', { type: 'file', accept: '.txt,.md', 'aria-label': 'Importer un descriptif texte' });
  file.addEventListener('change', async () => { const f = file.files[0]; if (f) brief.value = await f.text(); });
  main.append(h('h1', {}, 'Nouvelle mission'),
    h('div', { class: 'card' },
      h('div', { class: 'grid cols-2' }, field('Intitulé', title), field('Client', client)),
      field('Descriptif du besoin', brief, "Le titre ne suffit jamais : l'analyse identifie le travail réellement demandé. Rien n'est inventé : une information absente devient une question."),
      field('… ou importer un fichier texte', file),
      h('div', { class: 'row end' }, h('button', { class: 'primary', id: 'create-mission', onclick: () => guard(async () => {
        const m = await api('/missions', { method: 'POST', body: { client: client.value, title: title.value, brief: brief.value } });
        toast('Mission créée : vérifiez et validez les critères proposés.');
        location.hash = `#/mission/${m.id}/needs`;
      }) }, "Lancer l'analyse"))));
}

async function library(main) {
  const render = async () => {
    const q = $('#lib-q') ? $('#lib-q').value : '';
    const list = await api('/library' + (q ? `?q=${encodeURIComponent(q)}` : ''));
    const box = clear($('#lib-list'));
    if (!list.length) box.append(h('p', { class: 'muted' }, 'Aucune entrée. Les enseignements proposés par l’application apparaissent ici pour validation.'));
    for (const e of list) box.append(h('div', { class: 'card flat' },
      h('header', {}, h('h3', {}, e.title), chip(e.status === 'published' ? 'Publié' : e.status === 'retired' ? 'Retiré' : 'À valider', e.status === 'published' ? 'ok' : 'warn'),
        e.client_specific ? chip('Spécifique à un client', 'orange') : chip('Général', 'blue'), chip(e.kind.replace(/_/g, ' '), 'grey')),
      h('p', { class: 'excerpt' }, e.body), h('small', { class: 'muted' }, `${e.author} · v${e.version} · ${fmtDate(e.updated_at)}`),
      e.status !== 'published' ? h('div', { class: 'row' },
        h('button', { class: 'small', onclick: () => guard(async () => { await api(`/library/${e.id}/publish`, { method: 'POST', body: { generalize: false } }); render(); }) }, 'Valider et publier (reste spécifique)'),
        e.client_specific ? h('button', { class: 'small', onclick: () => guard(async () => { await api(`/library/${e.id}/publish`, { method: 'POST', body: { generalize: true } }); render(); }) }, 'Valider et généraliser') : null) : null));
  };
  const kind = h('select', {}, ['methode_recherche', 'booleen', 'enseignement_retour_client', 'faux_positif', 'question_qualification', 'erreur_matching', 'exemple_preuve', 'note_metier'].map(k => h('option', { value: k }, k.replace(/_/g, ' '))));
  const title = h('input', { type: 'text', maxlength: 300 }); const body = h('textarea', { maxlength: 8000, rows: 4 });
  main.append(h('h1', {}, 'Bibliothèque Talent'),
    h('p', { class: 'muted' }, 'Enseignements validés par les Talent Managers. Une exigence propre à un client ne devient jamais une règle universelle automatiquement.'),
    h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Proposer un enseignement')),
      h('div', { class: 'grid cols-2' }, field('Type', kind), field('Titre', title)), field('Contenu', body),
      h('div', { class: 'row end' }, h('button', { class: 'primary', onclick: () => guard(async () => { await api('/library', { method: 'POST', body: { kind: kind.value, title: title.value, body: body.value } }); title.value = body.value = ''; toast('Entrée proposée : elle reste privée tant qu’elle n’est pas validée.'); render(); }) }, 'Proposer'))),
    h('div', { class: 'row' }, h('input', { type: 'search', id: 'lib-q', placeholder: 'Rechercher…', 'aria-label': 'Rechercher dans la bibliothèque', oninput: () => render() })),
    h('div', { id: 'lib-list' }));
  await render();
}

function coach(main) {
  const q = h('textarea', { rows: 3, maxlength: 2000, placeholder: 'Ex. « Pourquoi cette recherche est-elle trop restrictive ? », « Quelle différence entre un consultant SAP AMOA et MOE ? »' });
  const out = h('div', { id: 'coach-out' });
  main.append(h('h1', {}, 'Talent Coach'), h('p', { class: 'muted' }, 'Des explications de raisonnement professionnel, pas une reformulation de score. Aucun classement des recruteurs n’est produit.'),
    h('div', { class: 'card' }, field('Votre question', q), h('div', { class: 'row end' }, h('button', { class: 'primary', onclick: () => guard(async () => {
      const r = await api('/coach', { method: 'POST', body: { question: q.value } });
      clear(out);
      for (const a of [...r.answers, ...r.related]) out.append(h('div', { class: 'card flat' }, h('h3', {}, a.title), h('p', {}, a.body)));
      out.append(h('small', { class: 'muted' }, r.note));
    }) }, 'Expliquer'))), out);
}

boot();
