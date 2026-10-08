import { $, api, clear, guard, h, toast } from './lib.js';

export function renderAssistant(mission, onChange) {
  const box = clear($('#assistant'));
  const log = h('div', { class: 'log', id: 'assistant-log', role: 'log', 'aria-live': 'polite' });
  const input = h('textarea', { rows: 3, maxlength: 3000, id: 'assistant-input', placeholder: 'Ex. « Rends la recherche moins stricte » · « Pourquoi 85 à C-0001 ? » · « Compare C-0001 et C-0002 » · « Le client vient de préciser que Kafka Connect est impératif »', 'aria-label': 'Message pour l’assistant' });
  const say = (cls, text) => { const m = h('div', { class: 'msg ' + cls }, text); log.append(m); log.scrollTop = log.scrollHeight; return m; };
  const send = () => guard(async () => {
    const msg = input.value.trim(); if (!msg) return;
    say('user', msg); input.value = '';
    const r = await api(`/missions/${mission.id}/assistant`, { method: 'POST', body: { message: msg } });
    const m = say('bot', r.reply);
    for (const p of r.proposals) {
      const row = h('div', { class: 'alert warn' }, h('b', {}, 'Proposition à confirmer'), h('div', {}, p.explanation),
        h('ul', { class: 'tight' }, p.consequences.map(c => h('li', {}, c))),
        h('div', { class: 'row' },
          h('button', { class: 'primary small', onclick: () => guard(async () => { await api(`/missions/${mission.id}/proposals/${p.id}/confirm`, { method: 'POST' }); row.replaceChildren(h('b', {}, 'Confirmée.')); toast('Proposition appliquée.'); onChange && onChange(); }) }, 'Confirmer'),
          h('button', { class: 'small', onclick: () => guard(async () => { await api(`/missions/${mission.id}/proposals/${p.id}/reject`, { method: 'POST' }); row.replaceChildren(h('b', {}, 'Rejetée.')); }) }, 'Rejeter')));
      log.append(row);
    }
    log.scrollTop = log.scrollHeight;
  });
  box.append(h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Assistant'), h('button', { class: 'small', onclick: () => { $('#assistant').hidden = true; } }, 'Fermer')),
    h('p', { class: 'hint' }, 'Il explique et propose. Tout changement de critère, de score ou d’information validée est confirmé par vous.'),
    log, input, h('div', { class: 'row end' }, h('button', { class: 'primary', id: 'assistant-send', onclick: send }, 'Envoyer')));
}
