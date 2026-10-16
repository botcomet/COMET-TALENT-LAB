import { api, chip, clear, fmtDate, guard, h } from './lib.js';

const ACCESS = { owner: 'Propriétaire', strategie: 'Stratégie', lecture: 'Lecture', edition: 'Édition' };

// Recherche transverse dans les missions accessibles : titres, clients, exigences, requêtes. Jamais les candidats ni les CV.
export async function renderHistory(main, query) {
  const q = h('input', { type: 'search', id: 'history-q', maxlength: 100, value: query || '', placeholder: 'Ex. « kafka », « architecte », « SAP AMOA »…', 'aria-label': 'Rechercher dans l’historique des missions' });
  const go = () => { const v = q.value.trim(); location.hash = v ? `#/history/${encodeURIComponent(v)}` : '#/history'; };
  q.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  main.append(h('h1', {}, 'Historique des missions'),
    h('p', { class: 'muted' }, 'Retrouver une mission passée, une exigence déjà rencontrée ou une requête déjà écrite, pour s’en inspirer. La recherche ne porte que sur vos missions et celles partagées avec vous, ' +
      'et jamais sur les candidats ni sur le contenu des CV.'),
    h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', { class: 'grow' }, q), h('button', { class: 'primary', id: 'history-go', onclick: go }, 'Rechercher'))));
  const out = h('div', { id: 'history-out' });
  main.append(out);
  if (!query || query.trim().length < 2) return out.append(h('p', { class: 'muted' }, 'Saisir au moins deux caractères. Tous les mots saisis doivent apparaître dans le même élément (intitulé, exigence ou requête).'));
  await guard(async () => {
    const r = await api(`/history/search?q=${encodeURIComponent(query.trim())}`);
    clear(out);
    if (!r.results.length) return out.append(h('div', { class: 'alert blue' }, `Aucun résultat pour « ${r.query} ».`, h('div', { class: 'hint' }, r.scope_note)));
    out.append(h('p', { class: 'muted', id: 'history-count' }, `${r.results.length} mission(s)${r.truncated ? ' — résultats limités : affinez votre recherche' : ''}.`));
    for (const res of r.results) {
      const m = res.mission;
      const deep = tab => `#/mission/${m.id}/${m.access === 'strategie' ? 'sourcing' : tab}`;
      out.append(h('div', { class: 'card flat history-result' },
        h('header', {}, h('h3', {}, h('a', { href: deep('needs') }, m.title)), m.client ? chip(m.client, 'grey') : null, chip(ACCESS[m.access] || m.access, m.access === 'owner' ? 'blue' : 'orange'),
          h('small', { class: 'muted' }, `Mis à jour ${fmtDate(m.updated_at)}`)),
        h('ul', { class: 'tight' }, res.hits.map(x => h('li', {}, h('a', { href: deep(x.kind === 'recherche' ? 'sourcing' : 'needs') }, chip(x.label, x.kind === 'exigence' ? 'blue' : x.kind === 'recherche' ? 'ok' : 'grey')),
          ' ', x.kind === 'recherche' ? h('code', {}, x.text) : x.text))),
        res.hit_count > res.hits.length ? h('small', { class: 'muted' }, `… et ${res.hit_count - res.hits.length} autre(s) correspondance(s) dans cette mission.`) : null));
    }
    out.append(h('p', { class: 'hint' }, r.scope_note));
  });
}
