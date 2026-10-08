import { $, api, bar, CATEGORIES, chip, clear, copyText, field, fmtDate, guard, h, levelChip, SOURCE_KINDS, STATUSES, STRATEGIES, tierChip, toast, TRIGGERS, LEVELS } from './lib.js';
import { renderAssistant } from './assistant.js';

let pollTimer = null;
export function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

export async function renderMission(main, id, tab, sub) {
  const m = await api(`/missions/${id}`);
  const strategyOnly = m.access === 'strategie';
  const canEdit = m.access === 'edition' || m.access === 'owner';
  const ctx = { m, id, canEdit, strategyOnly, reload: () => renderMission(clear($('#main')), id, tab, sub) };
  const tabs = strategyOnly ? [['sourcing', 'Sourcing']] : [['needs', 'Besoin & grille'], ['sourcing', 'Sourcing'], ['matching', 'Matching'], ['history', 'Historique'], ...(m.access === 'owner' ? [['share', 'Partage']] : [])];
  if (strategyOnly) tab = 'sourcing';
  main.append(
    h('div', { class: 'row' }, h('h1', { class: 'grow' }, m.title),
      m.client ? chip(m.client, 'grey') : null, chip(m.access === 'owner' ? 'Propriétaire' : `Accès : ${m.access}`, 'blue'), m.grid_version ? chip(`Grille v${m.grid_version} figée`, 'ok') : chip('Grille non figée', 'warn'),
      strategyOnly ? null : h('button', { id: 'open-assistant', onclick: () => { renderAssistant(m, ctx.reload); $('#assistant').hidden = false; $('#assistant-input').focus(); } }, 'Assistant')),
    strategyOnly ? h('div', { class: 'alert blue' }, 'Vous avez accès à la stratégie de sourcing de cette mission (recherches et explications) — aucune donnée candidat n’est partagée.') : null,
    m.grid_drift ? h('div', { class: 'alert warn', id: 'grid-drift' }, h('b', {}, `Le besoin a changé depuis la grille v${m.grid_drift.from_version} : `),
      `${m.grid_drift.added.length} critère(s) ajouté(s), ${m.grid_drift.removed.length} retiré(s), ${m.grid_drift.changed.length} modifié(s). Les scores restent calculés sur la grille v${m.grid_drift.from_version} (figée) — créer une nouvelle version, motivée et tracée, pour en tenir compte.`) : null,
    h('div', { class: 'tabs', role: 'tablist' }, tabs.map(([k, l]) => h('button', { role: 'tab', 'aria-selected': String(k === tab), id: `tab-${k}`, onclick: () => { location.hash = `#/mission/${id}/${k}`; } }, l))),
    h('div', { id: 'tabpane', role: 'tabpanel' }));
  const pane = $('#tabpane');
  if (tab === 'needs') await needs(pane, ctx);
  else if (tab === 'sourcing') await sourcing(pane, ctx);
  else if (tab === 'matching') await matching(pane, ctx, sub);
  else if (tab === 'history') await history(pane, ctx);
  else if (tab === 'share') await share(pane, ctx);
}

// =============================================================================== BESOIN & GRILLE
async function needs(pane, { m, id, canEdit, reload }) {
  const a = m.analysis || {};
  const eff = new Set(m.effective_requirement_ids);
  pane.append(
    h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Ce que la mission demande réellement')),
      h('p', {}, a.real_work_summary || ''),
      ...(a.warnings || []).map(w => h('div', { class: 'alert warn' }, w)),
      a.activities && a.activities.length ? h('p', {}, 'Activités détectées : ', a.activities.map(x => chip(x.label, x.in_tasks ? 'blue' : 'grey'))) : null,
      Object.keys(a.modalities || {}).length ? h('dl', { class: 'kv' }, Object.entries(a.modalities).flatMap(([k, v]) => [h('dt', {}, k.replace(/_/g, ' ')), h('dd', {}, String(v))])) : null,
      (a.missing_info || []).length ? h('details', {}, h('summary', {}, `Informations absentes du brief (${a.missing_info.length}) — à demander, jamais supposées`), h('ul', { class: 'tight' }, a.missing_info.map(x => h('li', {}, x.question)))) : null),
    h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Sources du besoin'), h('small', { class: 'muted' }, 'Impératif client confirmé > précision validée > retour d’entretien > brief officiel > description initiale')),
      h('table', {}, h('tbody', {}, m.sources.map(s => h('tr', {}, h('td', {}, chip(SOURCE_KINDS[s.kind] || s.kind, 'grey')), h('td', {}, s.author || '—'), h('td', {}, s.source_date || fmtDate(s.created_at)), h('td', {}, h('details', {}, h('summary', {}, s.text.slice(0, 90) + (s.text.length > 90 ? '…' : '')), h('div', { class: 'excerpt' }, s.text))))))),
      canEdit ? sourceForm(id, reload) : null));
  // conflits
  if (m.conflicts.length) pane.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Sources divergentes')), m.conflicts.map(c => h('div', { class: 'alert ' + (c.needs_review ? 'bad' : 'warn') }, `${c.key} — ${c.reason}`))));
  // exigences
  const reqCard = h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Exigences — classification obligatoire'),
    canEdit ? h('button', { id: 'validate-reqs', class: 'primary small', onclick: () => guard(async () => { const r = await api(`/missions/${id}/requirements/validate`, { method: 'POST', body: {} }); toast(`${r.validated} exigence(s) validée(s) (les éliminatoires exigent une confirmation client tracée).`); reload(); }) }, 'Valider les exigences proposées') : null),
    h('p', { class: 'hint' }, 'Aucune compétence n’est éliminatoire parce qu’elle figure dans une liste. Les propositions sont à valider par un recruteur.'),
    h('div', { class: 'table-wrap' }, h('table', { id: 'req-table' },
      h('thead', {}, h('tr', {}, ['Exigence', 'Catégorie', 'Profondeur', 'Provenance', 'Statut'].map(t => h('th', {}, t)))),
      h('tbody', {}, m.requirements.filter(r => r.status === 'active').map(r => reqRow(r, id, canEdit, eff.has(r.id), reload))))),
    canEdit ? addReq(id, reload) : null);
  pane.append(reqCard);
  if (m.knowledge_suggestions && m.knowledge_suggestions.length) pane.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Enseignements sur des missions comparables')),
    m.knowledge_suggestions.map(k => h('div', { class: 'alert blue' }, h('b', {}, k.title), ' — ', k.body.slice(0, 220), k.caution ? h('div', { class: 'hint' }, k.caution) : null))));
  pane.append(await gridCard(m, id, canEdit, reload));
  pane.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Descriptif complet pour un échange candidat')), h('button', { onclick: () => guard(async () => {
    const d = await api(`/missions/${id}/candidate-description`); const box = $('#cdesc'); clear(box).append(h('div', { class: 'query' }, d.text), h('p', { class: 'hint' }, d.note), h('button', { class: 'small', onclick: () => copyText(d.text) }, 'Copier'));
  }) }, 'Générer le descriptif'), h('div', { id: 'cdesc' })));
}

function sourceForm(id, reload) {
  const kind = h('select', {}, ['client_precision_validee', 'client_imperatif_confirme', 'retour_entretien', 'note_brief_client', 'description_initiale', 'brief_officiel'].map(k => h('option', { value: k }, SOURCE_KINDS[k])));
  const author = h('input', { type: 'text', maxlength: 200, placeholder: 'Qui l’a dit ? (représentant client autorisé)' }); const date = h('input', { type: 'date' });
  const text = h('textarea', { rows: 3, maxlength: 60000, placeholder: 'Précision, note d’appel client, retour d’entretien…' });
  return h('details', {}, h('summary', {}, 'Ajouter une précision (appel client, retour d’entretien…)'),
    h('div', { class: 'grid cols-2' }, field('Type de source', kind), field('Auteur (obligatoire pour une précision client)', author)), field('Date', date), field('Texte', text),
    h('div', { class: 'row end' }, h('button', { id: 'add-source', class: 'primary', onclick: () => guard(async () => {
      await api(`/missions/${id}/sources`, { method: 'POST', body: { kind: kind.value, text: text.value, author: author.value, source_date: date.value || null } });
      toast('Source ajoutée : les nouvelles exigences sont proposées, jamais appliquées sans validation.'); reload();
    }) }, 'Ajouter')));
}

function reqRow(r, id, canEdit, effective, reload) {
  const patch = body => guard(async () => { await api(`/missions/${id}/requirements/${r.id}`, { method: 'PATCH', body }); reload(); });
  const cat = h('select', { 'aria-label': `Catégorie de ${r.label}`, disabled: !canEdit || r.dimension === 'contrainte' && false }, Object.entries(CATEGORIES).map(([k, v]) => h('option', { value: k, selected: k === r.category }, v)));
  cat.addEventListener('change', () => {
    if (cat.value === 'eliminatoire_confirme') {
      const q = window.prompt('Un critère éliminatoire doit être explicitement confirmé par le client. Citer l’extrait de la confirmation :', r.quote || '');
      if (!q) { cat.value = r.category; return; }
      patch({ category: cat.value, source_kind: 'client_precision_validee', quote: q });
    } else patch({ category: cat.value });
  });
  const depth = h('select', { 'aria-label': `Profondeur de ${r.label}`, disabled: !canEdit }, [['practice', 'Pratique'], ['advanced', 'Avancée']].map(([k, v]) => h('option', { value: k, selected: k === r.depth_required }, v)));
  depth.addEventListener('change', () => patch({ depth_required: depth.value }));
  return h('tr', { class: effective ? '' : 'muted' },
    h('td', {}, h('b', {}, r.label), h('div', { class: 'hint' }, r.kind === 'constraint' ? 'Contrainte — évaluée séparément du score technique' : (r.rationale || ''))),
    h('td', {}, canEdit ? cat : chip(CATEGORIES[r.category], 'grey')), h('td', {}, r.kind === 'skill' || r.kind === 'activity' ? (canEdit ? depth : chip(r.depth_required, 'grey')) : '—'),
    h('td', {}, chip(SOURCE_KINDS[r.source_kind] || r.source_kind, r.rank <= 2 ? 'blue' : 'grey'), r.source_author ? h('div', { class: 'hint' }, r.source_author) : null),
    h('td', {}, r.validated ? chip('Validée', 'ok') : chip('À valider', 'warn'), !effective ? h('div', { class: 'hint' }, 'Remplacée / écartée') : null, canEdit ? h('button', { class: 'link', onclick: () => patch({ status: 'rejected' }) }, 'Écarter') : null));
}

function addReq(id, reload) {
  const label = h('input', { type: 'text', maxlength: 200, placeholder: 'Ex. Kafka Connect' });
  const cat = h('select', {}, Object.entries(CATEGORIES).filter(([k]) => k !== 'eliminatoire_confirme').map(([k, v]) => h('option', { value: k, selected: k === 'souhaitable' }, v)));
  return h('details', {}, h('summary', {}, 'Ajouter une exigence'), h('div', { class: 'row' }, field('Libellé', label), field('Catégorie', cat),
    h('button', { class: 'primary', onclick: () => guard(async () => { await api(`/missions/${id}/requirements`, { method: 'POST', body: { label: label.value, category: cat.value } }); reload(); }) }, 'Ajouter')));
}

async function gridCard(m, id, canEdit, reload) {
  const grids = m.grids || [];
  const card = h('div', { class: 'card', id: 'grid-card' }, h('header', {}, h('h2', {}, 'Grille de scoring de la mission'), h('small', { class: 'muted' }, 'Propre à cette mission · 100 points · figée pendant la comparaison')));
  const draft = grids.filter(g => g.status === 'draft').pop();
  const frozen = grids.filter(g => g.status === 'frozen').pop();
  if (!grids.length) {
    card.append(h('p', {}, 'Aucune grille. Validez d’abord les exigences, puis proposez une répartition des poids.'),
      canEdit ? h('button', { id: 'propose-grid', class: 'primary', onclick: () => guard(async () => { await api(`/missions/${id}/grid/propose`, { method: 'POST' }); reload(); }) }, 'Proposer une grille') : null);
    return card;
  }
  const g = draft && (!frozen || draft.version > frozen.version) ? draft : frozen;
  const inputs = {};
  card.append(h('div', { class: 'row' }, g.status === 'frozen' ? chip(`v${g.version} figée`, 'ok') : chip(`v${g.version} brouillon`, 'warn'), h('span', { class: 'count', id: 'grid-total' }, `Total : ${g.total_weight}/100`),
    g.status === 'frozen' ? h('small', { class: 'muted' }, `empreinte ${g.content_hash.slice(0, 12)}… · validée le ${fmtDate(g.validated_at)}`) : null),
    g.reason ? h('p', { class: 'hint' }, `Motif : ${g.reason}`) : null,
    h('div', { class: 'table-wrap' }, h('table', { id: 'grid-table' }, h('thead', {}, h('tr', {}, ['Critère', 'Catégorie', 'Points', 'Source'].map(t => h('th', {}, t)))), h('tbody', {},
      g.criteria.map(c => h('tr', {}, h('td', {}, c.label, c.members && c.members.length ? h('div', { class: 'hint' }, `Au moins ${c.params.at_least} parmi : ${c.members.length} technologies`) : null),
        h('td', {}, chip(CATEGORIES[c.category] || c.category, 'grey')),
        h('td', { class: 'num' }, c.scored ? (g.status === 'draft' && canEdit ? (inputs[c.key] = h('input', { type: 'number', min: 0, max: 100, value: c.weight, 'aria-label': `Poids de ${c.label}`, style: undefined })) : String(c.weight)) : h('small', { class: 'muted' }, c.dimension === 'contrainte' ? 'contrainte (hors score)' : 'non noté')),
        h('td', {}, h('small', {}, c.source_kind.replace(/_/g, ' ')))))))));
  const out = h('div', { id: 'grid-check' });
  card.append(out);
  if (g.status === 'draft' && canEdit) {
    const save = async () => { const w = {}; for (const [k, el] of Object.entries(inputs)) w[k] = parseInt(el.value || '0', 10); await api(`/missions/${id}/grid/${g.id}`, { method: 'PUT', body: { weights: w } }); };
    const refresh = async () => { const r = await api(`/missions/${id}/grid/${g.id}/check`); clear(out).append(...r.errors.map(e => h('div', { class: 'alert bad' }, e)), ...r.warnings.map(e => h('div', { class: 'alert warn' }, e))); };
    card.append(h('div', { class: 'row end' },
      h('button', { id: 'check-grid', onclick: () => guard(async () => { await save(); await refresh(); }) }, 'Enregistrer et vérifier'),
      h('button', { id: 'freeze-grid', class: 'primary', onclick: () => guard(async () => {
        await save();
        const ok = window.confirm('Valider et figer cette grille ? Les critères et les poids ne changeront plus pour la comparaison des candidats (une nouvelle version sera nécessaire).');
        if (!ok) return;
        await api(`/missions/${id}/grid/${g.id}/validate`, { method: 'POST', body: { allow_unresolved_clarifications: true } }); toast('Grille figée.'); reload();
      }) }, 'Valider et figer la grille')));
    await guard(refresh);
  } else if (g.status === 'frozen' && canEdit) {
    const reason = h('input', { type: 'text', maxlength: 500, placeholder: 'Pourquoi le besoin change (ex. retour client)' });
    card.append(h('details', {}, h('summary', {}, 'Le besoin client change : créer une nouvelle version'), h('div', { class: 'row' }, field('Motif', reason),
      h('button', { id: 'new-version', class: 'primary', onclick: () => guard(async () => { const r = await api(`/missions/${id}/grid/new-version`, { method: 'POST', body: { reason: reason.value } }); toast(`Version v${r.grid.version} en brouillon — ${r.candidates_to_reassess} candidat(s) à réévaluer après validation.`); reload(); }) }, 'Créer la version'))));
  }
  return card;
}

// =============================================================================== SOURCING
async function sourcing(pane, { m, id, canEdit, reload }) {
  const list = await api(`/missions/${id}/searches`);
  const lineages = {};
  for (const s of list) (lineages[s.lineage] ||= []).push(s);
  pane.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Recherches booléennes'), canEdit ? h('button', { id: 'gen-searches', class: 'primary', onclick: () => guard(async () => { await api(`/missions/${id}/searches/generate`, { method: 'POST' }); reload(); }) }, 'Générer trois recherches') : null),
    h('p', { class: 'hint' }, 'Le booléen sert à découvrir ; le scoring vérifie ensuite strictement. La syntaxe Turnover n’est pas confirmée : tester la requête sur la plateforme, puis renseigner le nombre de résultats réel — aucun nombre n’est simulé.'),
    canEdit ? customForm(id, reload) : null));
  if (!list.length) pane.append(h('p', { class: 'muted' }, 'Aucune recherche. Les exigences valides sont nécessaires pour générer des recherches.'));
  const order = ['exploratory', 'balanced', 'strict'];
  const heads = Object.values(lineages).map(l => l.sort((a, b) => a.version - b.version)).sort((a, b) => order.indexOf(a[0].strategy) - order.indexOf(b[0].strategy) || a[0].created_at.localeCompare(b[0].created_at));
  for (const chain of heads) pane.append(searchCard(chain, id, canEdit, reload));
}

function customForm(id, reload) {
  const q = h('textarea', { rows: 2, maxlength: 2000, placeholder: 'Coller ou saisir une requête pour la contrôler (parenthèses, priorité AND/OR, longueur)…', 'aria-label': 'Requête à contrôler' });
  const res = h('div', { id: 'custom-result' });
  return h('details', {}, h('summary', {}, 'Contrôler / ajouter ma propre requête'), q, h('div', { class: 'row' },
    h('button', { onclick: () => guard(async () => {
      const r = await api('/tools/boolean/validate', { method: 'POST', body: { query: q.value } });
      clear(res).append(h('div', { class: 'alert ' + (r.valid ? 'ok' : 'bad') }, `${r.valid ? 'Syntaxe valide' : 'Requête invalide'} — ${r.length}/${r.max_length} caractères`), ...r.issues.map(i => h('div', { class: 'alert ' + (i.severity === 'error' ? 'bad' : 'warn') }, i.message)));
    }) }, 'Contrôler'),
    h('button', { class: 'primary', onclick: () => guard(async () => { await api(`/missions/${id}/searches/custom`, { method: 'POST', body: { query: q.value } }); reload(); }) }, 'Ajouter à la mission')), res);
}

function searchCard(chain, id, canEdit, reload) {
  const s = chain[chain.length - 1];
  const e = s.explanation || {};
  const over = s.length > (e.max_length || 250);
  const card = h('div', { class: 'card', dataset: { strategy: s.strategy } });
  card.append(h('header', {}, h('h2', {}, STRATEGIES[s.strategy]), chip(`version ${s.version}`, 'grey'), s.status === 'useful' ? chip('Utile', 'ok') : s.status === 'saved' ? chip('Sauvegardée', 'blue') : null,
    e.low_volume_risk ? chip(`risque de faible volume : ${e.low_volume_risk}`, e.low_volume_risk === 'élevé' ? 'bad' : e.low_volume_risk === 'modéré' ? 'warn' : 'ok') : null,
    h('span', { class: 'grow' }), h('span', { class: 'count' + (over ? ' over' : '') }, `${s.length}/${e.max_length || 250} car.`),
    h('button', { class: 'small', onclick: () => copyText(s.query) }, 'Copier')),
    h('div', { class: 'query', 'data-testid': 'query' }, s.query));
  for (const x of s.extra_queries || []) card.append(h('div', { class: 'row' }, h('small', { class: 'muted' }, 'Requête complémentaire (à exécuter aussi) :'), h('button', { class: 'small', onclick: () => copyText(x) }, 'Copier')), h('div', { class: 'query' }, x));
  if (s.modification) card.append(h('div', { class: 'alert blue' }, h('b', {}, 'Modification : '), s.modification));
  const list = (title, items, render) => items && items.length ? h('details', {}, h('summary', {}, title), h('ul', { class: 'tight' }, items.map(render))) : null;
  card.append(
    list('Intitulés retenus', e.titles_kept, t => h('li', {}, `${t.term} — ${t.why}`)),
    list('Technologies imposées', e.imposed_technologies, t => h('li', {}, h('b', {}, t.term), ` : ${(t.terms || []).join(' OR ') || 'combinaison'} — ${t.why}`)),
    list('Synonymes utilisés', e.synonyms_used, t => h('li', {}, `${t.group} : ${t.terms.join(', ')}`)),
    list('Compétences volontairement exclues du booléen', e.excluded_skills, t => h('li', {}, h('b', {}, t.skill), ` — ${t.reason}`)),
    list('Risques de faux positifs', e.false_positive_risks, t => h('li', {}, t)),
    list('Risques de faux négatifs', e.false_negative_risks, t => h('li', {}, t)),
    list('Avertissements', e.warnings, t => h('li', {}, t)));
  if (chain.length > 1) card.append(h('details', {}, h('summary', {}, `Historique des versions (${chain.length})`), h('ol', { class: 'tight' }, chain.map(v => h('li', {}, h('div', { class: 'query' }, v.query), h('small', {}, v.modification),
    (v.feedbacks || []).map(f => h('div', { class: 'hint' }, `Résultat saisi : ${f.result_count ?? '?'}${f.relevance ? ' · pertinence ' + f.relevance : ''} → ${f.diagnosis.problem}`)))))));
  if (canEdit) card.append(feedbackForm(s, id, reload));
  return card;
}

function feedbackForm(s, id, reload) {
  const count = h('input', { type: 'number', min: 0, id: `fb-count-${s.id}`, placeholder: 'ex. 0, 45, 12000', 'aria-label': 'Nombre de résultats sur la plateforme' });
  const rel = h('select', { 'aria-label': 'Pertinence des premiers CV' }, [['', '—'], ['bonne', 'Bonne'], ['partielle', 'Partielle'], ['mauvaise', 'Mauvaise']].map(([k, v]) => h('option', { value: k }, v)));
  const tagDefs = [['trop_juniors', 'Profils trop juniors'], ['mauvaise_expertise', 'Mauvaises expertises'], ['competence_absente', 'Compétence absente'], ['faux_positifs_recurrents', 'Faux positifs récurrents'], ['bons_profils_manquants', 'Bons profils manquants']];
  const tags = tagDefs.map(([k, l]) => [k, h('input', { type: 'checkbox', value: k, 'aria-label': l }), l]);
  const fp = h('input', { type: 'text', placeholder: 'Termes des faux positifs (séparés par des virgules)' }); const miss = h('input', { type: 'text', placeholder: 'Compétence absente des premiers CV' });
  const notes = h('input', { type: 'text', placeholder: 'Autres observations' }); const out = h('div', { id: `fb-out-${s.id}` });
  return h('details', { open: s.version === 1 && !(s.feedbacks || []).length ? false : false }, h('summary', {}, 'J’ai exécuté cette recherche : saisir le résultat'),
    h('div', { class: 'grid cols-2' }, field('Nombre de résultats (réel, sur la plateforme)', count), field('Pertinence des premiers CV', rel)),
    h('div', { class: 'row' }, tags.map(([k, el, l]) => h('label', { class: 'row', style: undefined }, el, l))),
    h('div', { class: 'grid cols-2' }, field('Faux positifs récurrents', fp), field('Compétence absente', miss)), field('Observations', notes),
    h('div', { class: 'row end' },
      h('button', { class: 'primary', id: `fb-submit-${s.id}`, onclick: () => guard(async () => {
        const r = await api(`/missions/${id}/searches/${s.id}/feedback`, { method: 'POST', body: {
          result_count: count.value === '' ? null : parseInt(count.value, 10), relevance: rel.value || null, tags: tags.filter(([, el]) => el.checked).map(([k]) => k),
          false_positive_terms: fp.value.split(',').map(x => x.trim()).filter(Boolean), missing_skill: miss.value, notes: notes.value } });
        clear(out).append(h('div', { class: 'alert warn' }, h('b', {}, 'Diagnostic : '), r.diagnosis.problem.replace('_', ' '), h('ul', { class: 'tight' }, [...r.diagnosis.causes, ...r.diagnosis.advice].map(c => h('li', {}, c)))),
          r.modification ? h('div', { class: 'alert blue' }, h('b', {}, 'Nouvelle version : '), r.modification, h('ul', { class: 'tight' }, r.tradeoffs.map(t => h('li', {}, t)))) : null,
          r.needs_human ? h('div', { class: 'alert bad' }, r.needs_human) : null, ...(r.native_filters || []).map(f => h('div', { class: 'alert blue' }, f)));
        if (r.new_search) setTimeout(reload, 1500);
      }) }, 'Optimiser'),
      h('button', { onclick: () => guard(async () => { await api(`/missions/${id}/searches/${s.id}/save`, { method: 'POST', body: { status: 'useful', note: notes.value } }); toast('Version sauvegardée comme utile.'); reload(); }) }, 'Sauvegarder comme utile')), out);
}

// =============================================================================== MATCHING
async function matching(pane, { m, id, canEdit, reload }, sub) {
  const hasGrid = !!m.grid_version;
  const files = h('input', { type: 'file', multiple: true, accept: '.pdf,.docx,.txt', id: 'cv-files', 'aria-label': 'CV à importer' });
  pane.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Import et analyse des CV'), h('small', { class: 'muted' }, 'Jusqu’à 20 CV par lot · PDF textuels, DOCX, texte')),
    !hasGrid ? h('div', { class: 'alert warn' }, 'La grille n’est pas figée : les CV seront extraits mais évalués seulement après validation de la grille (mêmes critères pour tous).') : null,
    canEdit ? h('div', { class: 'row' }, files, h('button', { class: 'primary', id: 'upload-cvs', onclick: () => guard(async () => {
      if (!files.files.length) return toast('Sélectionnez des fichiers.', true);
      await api(`/missions/${id}/cvs`, { method: 'POST', files: [...files.files] }); files.value = ''; await refresh();
    }) }, 'Analyser le lot')) : null,
    h('div', { id: 'docs' }), ));
  pane.append(h('div', { id: 'cands' }), h('div', { id: 'cand-detail' }));
  const refresh = async () => {
    const docs = await api(`/missions/${id}/documents`);
    const box = clear($('#docs'));
    if (docs.length) box.append(h('div', { class: 'table-wrap' }, h('table', { id: 'doc-table' }, h('thead', {}, h('tr', {}, ['Document', 'Statut', 'Progression', 'Détail'].map(t => h('th', {}, t)))), h('tbody', {}, docs.map(d => {
      const tone = d.status === 'done' ? 'ok' : d.status === 'failed' ? 'bad' : d.status === 'duplicate' ? 'grey' : 'warn';
      return h('tr', { dataset: { status: d.status } }, h('td', {}, d.filename), h('td', {}, chip({ queued: 'En file', processing: 'En cours', done: 'Analysé', failed: 'Échec', duplicate: 'Doublon' }[d.status], tone)),
        h('td', {}, bar(d.progress, 100, d.status === 'failed' ? 'fail' : d.status === 'duplicate' ? 'dup' : '')), h('td', {}, d.status === 'failed' ? h('b', {}, d.error_message) : d.stage, (d.warnings || []).map(w => h('div', { class: 'hint' }, w)), (d.security_flags || []).map(f => h('div', { class: 'alert bad' }, `Alerte sécurité (${f.type}) : ${f.handling}`))));
    })))));
    await candidates();
    return docs;
  };
  const candidates = async () => {
    const view = window.__workView ? 'work' : 'all';
    const list = await api(`/missions/${id}/candidates?view=${view}`);
    const box = clear($('#cands'));
    const sel = new Set(window.__sel || []);
    const wv = h('input', { type: 'checkbox', id: 'work-view', checked: !!window.__workView, 'aria-label': 'Vue de travail exigeante' });
    wv.addEventListener('change', () => { window.__workView = wv.checked; candidates(); });
    const compareBtn = h('button', { id: 'compare-btn', disabled: true, onclick: () => guard(async () => compare([...sel])) }, 'Comparer');
    const syncBtn = () => { compareBtn.disabled = sel.size < 2; };
    box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, `Candidats (${list.length})`), h('span', { class: 'grow' }),
      h('label', { class: 'row' }, wv, 'Vue de travail exigeante (masque < 95/100 — tous restent consultables)'), compareBtn),
      list.length ? h('div', { class: 'table-wrap' }, h('table', { id: 'cand-table' }, h('thead', {}, h('tr', {}, ['', 'Réf.', 'Libellé', 'Documenté', 'Potentiel à confirmer', 'Niveau', 'Incertitude', 'Impératifs à vérifier', 'Statut'].map(t => h('th', {}, t)))), h('tbody', {}, list.map(c => {
        const a = c.assessment; const cb = h('input', { type: 'checkbox', 'aria-label': `Sélectionner ${c.ref}`, checked: sel.has(c.id) });
        cb.addEventListener('click', e => e.stopPropagation()); cb.addEventListener('change', () => { cb.checked ? sel.add(c.id) : sel.delete(c.id); window.__sel = [...sel]; syncBtn(); });
        return h('tr', { class: 'clickable' + (sub === c.id ? ' selected' : ''), dataset: { ref: c.ref }, tabindex: 0, onclick: () => { location.hash = `#/mission/${id}/matching/${c.id}`; }, onkeydown: e => { if (e.key === 'Enter') location.hash = `#/mission/${id}/matching/${c.id}`; } },
          h('td', {}, cb), h('td', {}, h('b', {}, c.ref)), h('td', {}, c.label), h('td', { class: 'num' }, a ? h('div', {}, h('b', {}, String(a.score)), bar(a.score_documented)) : '—'),
          h('td', { class: 'num' }, a ? h('div', {}, String(Math.round(a.score_potential)), bar(a.score_potential, 100, 'pot')) : '—'), h('td', {}, a ? tierChip(a.tier) : chip('Non évalué', 'grey')),
          h('td', {}, a ? chip(a.uncertainty.label, a.uncertainty.label === 'faible' ? 'ok' : a.uncertainty.label === 'moyenne' ? 'warn' : 'orange') : '—'),
          h('td', {}, a && a.open_mandatory.length ? chip(`${a.open_mandatory.length} : ${a.open_mandatory.slice(0, 2).join(', ')}`, 'warn') : '—'), h('td', {}, STATUSES[c.status] || c.status));
      })))) : h('p', { class: 'muted' }, 'Aucun candidat analysé.')));
    syncBtn();
  };
  const compare = async ids => {
    const r = await api(`/missions/${id}/compare`, { method: 'POST', body: { candidate_ids: ids } });
    const box = clear($('#cand-detail'));
    box.append(h('div', { class: 'card', id: 'compare-card' }, h('header', {}, h('h2', {}, `Comparaison — grille v${r.grid_version} identique pour tous`), h('button', { class: 'small', onclick: () => clear(box) }, 'Fermer')),
      h('p', {}, 'Classement : ', r.ranking.map((n, i) => h('span', {}, i ? ' > ' : '', h('b', {}, n), ` (${r.summary[n].score})`))),
      ...(r.why.main_differences || []).map(d => h('div', { class: 'alert blue' }, `${d.criterion} : ${r.why.leader} ${d.leader_points} pts (${(LEVELS[d.leader_level] || [d.leader_level])[0]}) contre ${r.why.second} ${d.second_points} pts (${(LEVELS[d.second_level] || [d.second_level])[0]})`)),
      h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Critère'), h('th', { class: 'num' }, 'Poids'), r.candidates.map(n => h('th', {}, n)))),
        h('tbody', {}, r.rows.map(row => h('tr', {}, h('td', {}, row.label), h('td', { class: 'num' }, row.weight), r.candidates.map(n => h('td', {}, levelChip(row.cells[n].level), h('div', { class: 'num' }, `${row.cells[n].points} pts`))))))))));
    box.scrollIntoView({ behavior: 'smooth' });
  };
  await refresh();
  if (sub) await candidateDetail(id, sub, canEdit, candidates);
  // suivi de progression tant que des documents sont en cours de traitement
  stopPolling();
  pollTimer = setInterval(async () => { try { const d = await refresh(); if (!d.some(x => x.status === 'queued' || x.status === 'processing')) stopPolling(); } catch { stopPolling(); } }, 1200);
}

async function candidateDetail(id, cid, canEdit, reloadList) {
  const c = await api(`/missions/${id}/candidates/${cid}`);
  const box = clear($('#cand-detail'));
  const a = c.assessment_full && c.assessment_full.result;
  const reload = async () => { await candidateDetail(id, cid, canEdit, reloadList); await reloadList(); };
  const head = h('div', { class: 'card', id: 'cand-card' }, h('header', {}, h('h2', {}, `${c.ref} — ${c.label || ''}`), c.document ? chip(`${c.document.pages} p. · extraction ${c.document.quality || '—'}`, c.document.quality === 'ok' ? 'ok' : 'warn') : null));
  box.append(head);
  if (!a) { head.append(h('div', { class: 'alert warn' }, 'Pas d’évaluation : aucune note n’est inventée. Valider la grille puis réévaluer.')); return; }
  head.append(h('div', { class: 'row' },
    h('div', {}, h('div', { class: 'score', id: 'score-doc' }, String(a.score_displayed), h('small', {}, ' / 100 documenté')), h('div', { class: 'hint' }, `Potentiel à confirmer : ${Math.round(a.score_potential)} (borne haute — pas une compétence acquise)`)),
    tierChip(a.tier), chip(`incertitude ${a.uncertainty.label}`, a.uncertainty.label === 'faible' ? 'ok' : 'warn'),
    h('div', { class: 'hint' }, `Qualité des informations : ${a.information_quality.score}/100 (≠ adéquation, ≠ qualité de rédaction)`)),
    h('p', {}, a.summary),
    ...a.alerts.map(x => h('div', { class: 'alert ' + (x.severity === 'bloquant' ? 'bad' : x.severity === 'majeur' ? 'bad' : 'warn') }, h('b', {}, x.severity + ' : '), x.message)),
    ...(a.security_flags || []).map(f => h('div', { class: 'alert bad' }, h('b', {}, `Alerte sécurité — ${f.type} : `), f.handling)),
    ...a.flags.map(f => h('div', { class: 'alert warn' }, f)));
  // identité de travail & statut
  const acr = h('input', { type: 'text', maxlength: 10, value: c.acronym || '', placeholder: 'Acronyme autorisé (ex. DFI)', 'aria-label': 'Acronyme autorisé' });
  const st = h('select', { 'aria-label': 'Statut' }, Object.entries(STATUSES).map(([k, v]) => h('option', { value: k, selected: k === c.status }, v)));
  const cm = h('input', { type: 'text', maxlength: 1000, placeholder: 'Motif (obligatoire pour écarter)', value: c.status_comment || '' });
  head.append(canEdit ? h('div', { class: 'row' }, acr, h('button', { class: 'small', onclick: () => guard(async () => { await api(`/missions/${id}/candidates/${cid}`, { method: 'PATCH', body: { acronym: acr.value } }); toast('Acronyme enregistré.'); }) }, 'Enregistrer l’acronyme'),
    st, cm, h('button', { class: 'small', onclick: () => guard(async () => { await api(`/missions/${id}/candidates/${cid}/status`, { method: 'POST', body: { status: st.value, comment: cm.value } }); toast('Statut enregistré (décision humaine).'); reloadList(); }) }, 'Enregistrer le statut')) : null);
  // critères
  const crit = h('div', { class: 'card' }, h('header', {}, h('h2', {}, `Évaluation sur la grille v${a.grid_version}`)), h('div', { class: 'table-wrap' }, h('table', { id: 'crit-table' },
    h('thead', {}, h('tr', {}, ['Critère', 'Niveau de preuve', 'Points', 'Justification et preuves'].map(t => h('th', {}, t)))),
    h('tbody', {}, a.criteria.map(k => h('tr', { dataset: { key: k.key } }, h('td', {}, h('b', {}, k.label), h('div', { class: 'hint' }, CATEGORIES[k.category] || k.category)), h('td', {}, levelChip(k.level)),
      h('td', { class: 'num' }, `${k.points}/${k.weight}`),
      h('td', {}, h('div', {}, k.justification), (k.advanced_missing || []).length && k.level !== 'confirme_demontre' ? h('div', { class: 'hint' }, `Manque pour conclure : ${k.advanced_missing.slice(0, 4).join(', ')}`) : null,
        ...k.notes.filter(n => !k.justification.includes(n)).map(n => h('div', { class: 'hint' }, n)),
        ...k.contradictions.map(x => h('div', { class: 'alert warn' }, h('b', {}, `${x.type} conservée : `), `${x.statement} — ${x.resolution}`)),
        k.members && k.members.length ? h('details', {}, h('summary', {}, `Détail des ${k.members.length} technologies`), h('ul', { class: 'tight' }, k.members.map(mm => h('li', {}, `${mm.label} : `, levelChip(mm.level))))) : null,
        k.evidence.length ? h('details', {}, h('summary', {}, `Preuves (${k.evidence.length})`), k.evidence.map(e => h('div', { class: 'excerpt' }, e.excerpt, h('div', { class: 'hint' }, `${e.source === 'cv' ? 'CV' : e.source} · ${e.location}${e.company ? ' · ' + e.company : ''}${e.period ? ' · ' + e.period : ''} — ${e.note || ''}`)))) : null,
        canEdit ? h('button', { class: 'link', onclick: () => correction(id, cid, c.assessment_full.id, k, reload) }, 'Corriger ce niveau') : null)))))));
  box.append(crit);
  // contraintes
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Contraintes — distinctes de l’adéquation technique')), h('ul', { class: 'tight' }, a.constraints.map(k => h('li', {}, chip(k.status.replace('_', ' '), k.status === 'compatible' ? 'ok' : k.status === 'incompatible' ? 'bad' : k.status === 'inconnu' ? 'grey' : 'warn'), ` ${k.label} — ${k.explanation}`)))));
  // qualification
  const qbox = h('div', { id: 'questions' });
  const renderQ = q => { clear(qbox).append(h('h3', {}, 'Trois questions prioritaires'), ...q.priority.map(qq => questionCard(qq)), q.complementary.length ? h('h3', {}, 'Questions complémentaires') : null, ...q.complementary.map(qq => questionCard(qq))); };
  if (c.questions) renderQ(c.questions);
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Qualification'), h('button', { id: 'gen-questions', class: 'small primary', onclick: () => guard(async () => renderQ(await api(`/missions/${id}/candidates/${cid}/questions`, { method: 'POST' }))) }, 'Générer les questions')), qbox));
  // notes d'appel
  if (canEdit) box.append(noteCard(id, cid, reload));
  // preuves issues des échanges
  const ext = c.evidence.filter(e => !e.subject_key.startsWith('constraint:') || true);
  if (ext.length) box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Informations issues des échanges (à valider)')), h('ul', { class: 'tight', id: 'evidence-list' }, ext.map(e => h('li', {},
    chip(e.kind, e.kind === 'contredit' ? 'bad' : e.kind === 'limite' ? 'warn' : 'blue'), ' ', h('b', {}, e.subject_key.split(':')[1]), ' — ', e.excerpt, h('div', { class: 'hint' }, `${e.source.replace(/_/g, ' ')}${e.auto_generated ? ' (automatique)' : ''} · certitude ${e.certainty}${e.why_verify ? ' · ' + e.why_verify : ''}`),
    e.review ? chip(e.review === 'validated' ? 'Validée' : 'Rejetée', e.review === 'validated' ? 'ok' : 'bad') : (canEdit ? h('span', { class: 'row' },
      h('button', { class: 'small', onclick: () => guard(async () => { await api(`/missions/${id}/evidence/${e.id}/review`, { method: 'POST', body: { decision: 'validated' } }); reload(); }) }, 'Valider'),
      h('button', { class: 'small danger', onclick: () => guard(async () => { await api(`/missions/${id}/evidence/${e.id}/review`, { method: 'POST', body: { decision: 'rejected' } }); reload(); }) }, 'Rejeter')) : null))))));
  // historique des versions + diff
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Versions de l’analyse')), h('ol', { class: 'tight', id: 'versions' }, c.history.slice().reverse().map(v => h('li', {}, h('b', {}, `v${v.version}`), ` — ${TRIGGERS[v.trigger] || v.trigger} · ${fmtDate(v.created_at)} · ${v.score_documented}/100 · `, tierChip(v.tier), v.reason ? h('div', { class: 'hint' }, v.reason) : null)))),
    c.assessment_full.diff && c.assessment_full.diff.changes.length ? h('div', { class: 'alert blue', id: 'diff-box' }, h('b', {}, `Dernière modification : ${c.assessment_full.diff.score_delta >= 0 ? '+' : ''}${c.assessment_full.diff.score_delta} point(s)`),
      h('ul', { class: 'tight' }, c.assessment_full.diff.changes.map(x => h('li', {}, `${x.label} : ${(LEVELS[x.from] || [x.from || 'nouveau'])[0]} → ${(LEVELS[x.to] || [x.to])[0]} (${x.points_delta >= 0 ? '+' : ''}${x.points_delta} pt) ${x.because && x.because.length ? '— ' + x.because[0].slice(0, 120) : ''}`)))) : null);
  // exports
  const exp = h('div', { id: 'export-out' });
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Export pour DT Editor'), h('small', { class: 'muted' }, 'Matière fidèle et anonymisée — pas un second éditeur de dossier')),
    h('div', { class: 'row' }, h('button', { id: 'export-dt', onclick: () => guard(async () => {
      const r = await api(`/missions/${id}/candidates/${cid}/export/dt`);
      clear(exp).append(h('div', { class: 'query' }, r.markdown), h('div', { class: 'row' }, h('button', { class: 'small', onclick: () => copyText(JSON.stringify(r, null, 2)) }, 'Copier le JSON structuré')));
    }) }, 'Exporter la synthèse'),
      canEdit ? h('button', { id: 'llm-assist', onclick: () => guard(async () => { const r = await api(`/missions/${id}/candidates/${cid}/llm-assist`, { method: 'POST' }); toast(`${r.claims.filter(x => x.status === 'verified').length} passage(s) vérifié(s) par citation.`); reload(); }) }, 'Passages désignés par l’IA (optionnel)') : null), exp));
}

function questionCard(q) {
  return h('div', { class: 'card flat', dataset: { criterion: q.criterion_key } }, h('div', { class: 'row' }, chip(q.priority, q.priority === 'prioritaire' ? 'warn' : 'grey'), h('b', {}, q.criterion_label)),
    h('p', {}, q.text), h('div', { class: 'hint' }, `Réponse attendue : ${q.answer_type} · ${q.rationale}`),
    h('details', {}, h('summary', {}, 'Ce qui constitue une preuve / quand approfondir'), h('b', {}, 'Preuves :'), h('ul', { class: 'tight' }, q.proof_elements.map(x => h('li', {}, x))), h('b', {}, 'Approfondir si :'), h('ul', { class: 'tight' }, q.deepen_if.map(x => h('li', {}, x)))));
}

function noteCard(id, cid, reload) {
  const kind = h('select', {}, [['candidate_call_note', 'Notes d’appel candidat'], ['transcript', 'Transcription texte (automatique)'], ['interview_report', 'Compte rendu d’entretien'], ['complementary_doc', 'Document complémentaire autorisé']].map(([k, v]) => h('option', { value: k }, v)));
  const text = h('textarea', { rows: 5, maxlength: 100000, id: 'note-text', placeholder: 'Notes ou transcription. Une transcription automatique est une source à vérifier.' });
  const out = h('div', { id: 'note-out' });
  return h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Ajouter des informations d’appel')), field('Type', kind), field('Contenu', text),
    h('div', { class: 'row end' }, h('button', { id: 'add-note', class: 'primary', onclick: () => guard(async () => {
      const r = await api(`/missions/${id}/candidates/${cid}/notes`, { method: 'POST', body: { kind: kind.value, text: text.value, auto_generated: kind.value === 'transcript' } });
      text.value = '';
      clear(out).append(h('div', { class: 'alert ok' }, `${r.facts.length} information(s) extraite(s)${r.proposals.length ? ` · ${r.proposals.length} exigence(s) du poste proposée(s) (à confirmer, hors compétences du candidat)` : ''}.`),
        r.assessment && r.assessment.diff ? h('div', { class: 'alert blue' }, `Évaluation actualisée : ${r.assessment.diff.score_delta >= 0 ? '+' : ''}${r.assessment.diff.score_delta} point(s).`) : null);
      setTimeout(reload, 900);
    }) }, 'Extraire et actualiser')), out);
}

function correction(id, cid, asmId, k, reload) {
  const level = window.prompt(`Corriger « ${k.label} » — nouveau niveau :\n1 = confirmé et démontré\n2 = partiellement démontré\n3 = déclaré sans preuve\n4 = non documenté\n5 = contredit`, '3');
  const map = { 1: 'confirme_demontre', 2: 'partiellement_demontre', 3: 'declare_sans_preuve', 4: 'non_documente', 5: 'contredit' };
  if (!level || !map[level]) return;
  const nature = window.prompt('Nature de l’erreur (mention_surevaluee, contexte_deduit, role_surestime, confusion_portee, recence_ignoree, profondeur_surestimee, preuve_manquante, sous_evaluation, autre) :', 'mention_surevaluee');
  const comment = window.prompt('Commentaire (obligatoire, alimente un cas de non-régression ; aucune règle globale n’est modifiée) :', '');
  if (!nature || !comment) return;
  guard(async () => { await api(`/missions/${id}/candidates/${cid}/correction`, { method: 'POST', body: { assessment_id: asmId, criterion_key: k.key, new_level: map[level], error_nature: nature, comment } }); toast('Correction enregistrée et réévaluation faite.'); reload(); });
}

// =============================================================================== HISTORIQUE & PARTAGE
async function history(pane, { id }) {
  const h0 = await api(`/missions/${id}/history`);
  const detail = e => Object.entries(e.detail).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
  const rows = h0.events.map(e => h('tr', {}, h('td', {}, fmtDate(e.at)), h('td', {}, e.user), h('td', {}, chip(e.action, 'grey')), h('td', {}, h('small', {}, detail(e)))));
  const head = h('thead', {}, h('tr', {}, ['Date', 'Utilisateur', 'Action', 'Détail'].map(t => h('th', {}, t))));
  pane.append(
    h('div', { class: 'card' },
      h('header', {}, h('h2', {}, 'Historique de la mission'), h('small', { class: 'muted' }, 'Journal chaîné par hachage — sans contenu de CV')),
      h('div', { class: 'table-wrap' }, h('table', { id: 'audit-table' }, head, h('tbody', {}, rows)))),
    h('div', { class: 'card' },
      h('header', {}, h('h2', {}, 'Versions de grille')),
      h('ul', { class: 'tight' }, h0.grids.map(g => h('li', {}, `v${g.version} — ${g.status === 'frozen' ? 'figée' : 'brouillon'} — ${g.reason || ''}`)))));
}

async function share(pane, { id, reload }) {
  const list = await api(`/missions/${id}/shares`);
  const users = await api('/users');
  const sel = h('select', { id: 'share-user' }, users.map(u => h('option', { value: u.email }, `${u.display_name}${u.region ? ' (' + u.region + ')' : ''}`)));
  const scopes = [['strategie', 'Stratégie de sourcing seulement (recherches et explications, aucune donnée candidat)'], ['lecture', 'Lecture (mission, grille, candidats, évaluations)'], ['edition', 'Édition']];
  const scope = h('select', { id: 'share-scope' }, scopes.map(([k, v]) => h('option', { value: k }, v)));
  const grant = () => guard(async () => { await api(`/missions/${id}/shares`, { method: 'POST', body: { email: sel.value, scope: scope.value } }); toast('Accès accordé.'); reload(); });
  const revoke = s => () => guard(async () => { await api(`/missions/${id}/shares/${s.user_id}`, { method: 'DELETE' }); reload(); });
  const rows = list.map(s => h('tr', {}, h('td', {}, s.display_name), h('td', {}, chip(s.scope, 'blue')), h('td', {}, h('button', { class: 'small danger', onclick: revoke(s) }, 'Retirer'))));
  pane.append(h('div', { class: 'card' },
    h('header', {}, h('h2', {}, 'Partage')),
    h('p', { class: 'hint' }, 'Les missions sont privées par défaut. Partager une stratégie de sourcing ne divulgue jamais les CV ni les évaluations.'),
    h('div', { class: 'row' }, field('Collègue', sel), field('Niveau d’accès', scope), h('button', { class: 'primary', id: 'share-btn', onclick: grant }, 'Partager')),
    h('table', {}, h('tbody', {}, rows))));
}
