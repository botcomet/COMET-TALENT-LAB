import { api, chip, clear, field, fmtDate, guard, h, toast } from './lib.js';

const ROLES = { talent_manager: 'Talent Manager', pilote: 'Pilote', admin: 'Administrateur' };
const yesNo = (v, on = 'Activé', off = 'Désactivé') => chip(v ? on : off, v ? 'warn' : 'grey');

// Écran réservé aux administrateurs (l'accès est refusé par le serveur à tout autre rôle : ce contrôle n'est pas fait ici).
export async function renderAdmin(main, me) {
  main.append(h('h1', {}, 'Administration'),
    h('p', { class: 'muted' }, 'Comptes, état d’exploitation, conservation des données et intégrité du journal d’audit. Aucun secret n’est affiché sur cet écran.'),
    h('div', { id: 'admin-status' }), h('div', { id: 'admin-users' }), h('div', { id: 'admin-audit' }));
  await Promise.all([status(), users(me), auditCard()]);
}

async function status() {
  const box = clear(document.getElementById('admin-status'));
  const s = await api('/admin/status');
  const pend = s.purge_pending;
  const purge = h('button', { class: 'danger', id: 'purge-btn', disabled: pend.documents === 0 && pend.candidates === 0, onclick: () => guard(async () => {
    if (!window.confirm(`Supprimer DÉFINITIVEMENT ${pend.documents} document(s) et ${pend.candidates} candidat(s) dont la durée de conservation est dépassée ? Cette action est irréversible.`)) return;
    const r = await api('/admin/purge', { method: 'POST' });
    toast(`Purge effectuée : ${r.documents} document(s), ${r.candidates} candidat(s).`);
    await status();
  }) }, 'Purger maintenant');
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'État d’exploitation'), chip(`Environnement : ${s.env}`, s.env === 'prod' ? 'ok' : 'warn'), chip(`Authentification : ${s.auth_mode}`, 'grey')),
    h('dl', { class: 'kv' },
      h('dt', {}, 'Comptes actifs'), h('dd', {}, String(s.counts.users_active)),
      h('dt', {}, 'Missions'), h('dd', {}, String(s.counts.missions)),
      h('dt', {}, 'Fournisseur d’IA'), h('dd', {}, s.llm_provider || 'aucun'),
      h('dt', {}, 'Schéma de base'), h('dd', {}, s.schema.managed_by_migrations ? `révision ${s.schema.revision} ${s.schema.current ? '(à jour)' : `(attendue : ${s.schema.head})`}` : 'non géré par les migrations (mode développement)'),
      h('dt', {}, 'Maintenance planifiée'), h('dd', {}, yesNo(s.maintenance_enabled), s.maintenance_enabled ? ` toutes les ${s.maintenance_interval_hours} h` : ''),
      h('dt', {}, 'Purge automatique'), h('dd', {}, yesNo(s.retention_auto_purge)),
      h('dt', {}, 'Tête d’audit consignée hors base'), h('dd', {}, yesNo(s.audit_head_recorded_outside_db, 'Oui', 'Non'))),
    h('div', { class: s.maintenance_enabled ? 'hint' : 'alert warn' }, s.maintenance_enabled ? '' : 'La maintenance planifiée est désactivée : ni purge automatique ni consignation périodique de la tête d’audit.'),
    h('h3', {}, 'Conservation des données'),
    h('p', {}, `Durée de conservation configurée : ${s.retention_days} jours. ${s.retention_note}`),
    h('p', { id: 'purge-preview' }, pend.documents || pend.candidates ? `Une purge supprimerait ${pend.documents} document(s) et ${pend.candidates} candidat(s) (irréversible).` : 'Aucune donnée n’a dépassé la durée de conservation : rien à purger.'),
    h('div', { class: 'row' }, purge)));
}

async function users(me) {
  const box = clear(document.getElementById('admin-users'));
  const list = await api('/admin/users');
  const email = h('input', { type: 'email', id: 'new-user-email', maxlength: 255, placeholder: 'prenom.nom@…' });
  const name = h('input', { type: 'text', id: 'new-user-name', maxlength: 120 });
  const role = h('select', { id: 'new-user-role' }, Object.entries(ROLES).map(([k, v]) => h('option', { value: k }, v)));
  const patch = async (u, body, msg) => { await guard(async () => { await api(`/admin/users/${u.id}`, { method: 'PATCH', body }); toast(msg); }); await users(me); };   // recharge aussi après un refus
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Comptes'), h('small', { class: 'muted' }, 'Désactiver un compte ferme immédiatement ses sessions ouvertes. Le dernier administrateur actif ne peut pas être retiré.')),
    h('div', { class: 'table-wrap' }, h('table', { id: 'users-table' },
      h('thead', {}, h('tr', {}, ['Nom', 'Adresse', 'Rôle', 'Missions', 'Statut', ''].map(t => h('th', {}, t)))),
      h('tbody', {}, list.map(u => h('tr', { dataset: { email: u.email } },
        h('td', {}, h('b', {}, u.display_name), u.id === me.id ? chip('vous', 'blue') : null), h('td', {}, u.email),
        h('td', {}, h('select', { 'aria-label': `Rôle de ${u.display_name}`, onchange: e => patch(u, { role: e.target.value }, 'Rôle mis à jour.') },
          Object.entries(ROLES).map(([k, v]) => h('option', { value: k, selected: u.role === k }, v)))),
        h('td', { class: 'num' }, String(u.missions_owned)), h('td', {}, chip(u.active ? 'Actif' : 'Désactivé', u.active ? 'ok' : 'grey')),
        h('td', {}, h('button', { class: 'small' + (u.active ? ' danger' : ''), onclick: () => {
          if (u.active && !window.confirm(`Désactiver le compte de ${u.display_name} ? Ses sessions ouvertes seront fermées.`)) return;
          patch(u, { active: !u.active }, u.active ? 'Compte désactivé.' : 'Compte réactivé.');
        } }, u.active ? 'Désactiver' : 'Réactiver')))))) ),
    h('h3', {}, 'Ajouter un compte'),
    h('div', { class: 'grid cols-2' }, field('Adresse e-mail', email), field('Nom affiché', name)), field('Rôle', role),
    h('div', { class: 'row end' }, h('button', { class: 'primary', id: 'create-user', onclick: () => guard(async () => {
      await api('/admin/users', { method: 'POST', body: { email: email.value, display_name: name.value, role: role.value } });
      toast('Compte créé.'); await users(me);
    }) }, 'Créer le compte'))));
}

async function auditCard() {
  const box = clear(document.getElementById('admin-audit'));
  const out = h('div', { id: 'audit-result' });
  box.append(h('div', { class: 'card' }, h('header', {}, h('h2', {}, 'Journal d’audit')),
    h('p', {}, 'Vérifie que la chaîne d’événements n’a pas été altérée. Pour détecter aussi une suppression des derniers événements, consigner le numéro et l’empreinte de tête hors de la base.'),
    h('div', { class: 'row' }, h('button', { id: 'audit-verify', onclick: () => guard(async () => {
      const r = await api('/admin/audit/verify');
      clear(out);
      if (r.ok) out.append(h('div', { class: 'alert ok' }, `Chaîne intègre : ${r.checked} événement(s) vérifié(s).`), h('dl', { class: 'kv' }, h('dt', {}, 'Numéro de tête'), h('dd', {}, String(r.head_seq)), h('dt', {}, 'Empreinte de tête'), h('dd', {}, h('code', {}, r.head_hash))));
      else out.append(h('div', { class: 'alert bad' }, `CHAÎNE ROMPUE à l’événement n° ${r.broken_at_seq} (${r.checked} événement(s) vérifié(s) avant la rupture). Ne pas purger ni redémarrer : prévenir l’équipe technique.`));
      out.append(h('small', { class: 'muted' }, `Vérifié le ${fmtDate(new Date().toISOString())}`));
    }) }, 'Vérifier l’intégrité')), out));
}
