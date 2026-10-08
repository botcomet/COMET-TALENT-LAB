"""Extraction de faits depuis notes d'appel, transcriptions et entretiens (§7).

Une transcription n'est pas une vérité : chaque fait garde son locuteur, son
sujet, son niveau de certitude et sa source, et reste « à vérifier » tant qu'un
recruteur ne l'a pas validé. On distingue deux types d'information :

- une EXIGENCE CLIENT (« le client veut Kafka Connect ») → proposition de
  modification du besoin, jamais une compétence du candidat ;
- une PRÉCISION SUR L'EXPÉRIENCE du candidat (« je n'ai utilisé Kafka que sur
  deux topics ») → une preuve (ou une limite), jamais une modification du besoin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import clauses as cl
from . import lexicon as lx
from .enums import EvidenceKind, EvidenceSource, Level, Reliability
from .evidence import analyze_window, classify
from .text import fold, sentences

NOTE_KINDS = ("candidate_call_note", "transcript", "client_brief_note", "interview_report", "client_feedback", "complementary_doc")

_ROLE_HINTS = {
    "candidate": ("candidat", "freelance", "consultant", "prestataire", "independant"),
    "recruiter": ("recruteur", "recruteuse", "talent", "recrutement", "tm", "rh"),
    "client": ("client", "dsi", "manager", "hiring", "responsable", "directeur", "directrice"),
    "sales": ("commercial", "commerciale", "sales", "account", "kam", "ingenieur d'affaires", "bm"),
}
_SPEAKER = re.compile(r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?(?:\*\*)?([A-ZÀ-Ý][\wÀ-ÿ'’.()\- ]{0,38}?)(?:\*\*)?\s*[:：]\s+(\S.*)$")

_REQ_CUE = re.compile(r"\b(?:le\s+client|ils?|elle|il)\s+(?:veut|veulent|souhaite|souhaitent|exige|exigent|demande|demandent|cherche|cherchent|attend|attendent|precise|precisent|insiste|aimerait|prefererait)\b|"
                      r"\bc'est\s+(?:imperatif|obligatoire|indispensable|eliminatoire)\b|\bimperatif\b|\bindispensable\b|\bobligatoire\b|\bil\s+(?:nous\s+|leur\s+)?faut\b|\bil\s+(?:nous\s+|leur\s+)?faudrait\b|"
                      r"\bexige\b|\bvient\s+de\s+preciser\b")
_REQ_STRONG = re.compile(r"imperatif|indispensable|obligatoire|exige|exigent|absolument|incontournable|non\s+negociable|eliminatoire|veut|veulent|doit")
_REQ_DIFF = re.compile(r"fortement|tres\s+important|differenciant|prioritaire|determinant|prefere")
_REQ_SOFT = re.compile(r"souhait|aimerait|serait\s+un\s+plus|ideal|apprecie")
_LIMIT = re.compile(r"\bne\s+\w+(?:\s+\w+)?\s+que\b|\bn'(?:a|ai|avait|avais|etait|etais)\s+(?:\w+\s+){0,3}que\b|seulement|uniquement|\bjuste\b|simplement|a\s+la\s+marge|en\s+peripherie|"
                    r"\bun\s+peu\s+de\b|\bquelques\b|une\s+seule\s+fois|\bcontributeur\b|n'(?:etait|etais)\s+pas\s+(?:l')?(?:architecte|decisionnaire|responsable)|"
                    r"pas\s+decisionnaire|n'(?:avait|avais)\s+pas\s+la\s+main|sans\s+responsabilite|participe\s+seulement|"
                    r"\bonly\b|\bjust\b(?!\s+in\s+time)|\bmerely\b|\blimited\s+to\b|\bse\s+limite\w*\b|\bse\s+resume\w*\b|\blimite\w*\s+a\b|\brien\s+que\b|\ba\s+peine\b|"
                    r"\ba\s+couple\s+of\b|\ba\s+few\b|\bone\s+or\s+two\b|\bun\s+ou\s+deux\b|\bpas\s+plus\s+de\b")
_CONTRA = re.compile(r"\bjamais\s+(?:utilise|travaille|touche|fait|eu|pratique|manipule)|\bn'(?:a|ai|avait|avais)\s+(?:jamais|aucune?)\b|\bn'(?:a|ai|avait|avais)\s+pas\s+(?:utilise|travaille|touche|fait|eu|pratique)|"
                     r"aucune\s+experience|pas\s+d'experience|\bne\s+(?:connait|maitrise|pratique)\s+pas\b|jamais\s+pratique")
# absence DIRECTE de la technologie ; « je n'ai jamais administré le cluster Kafka » est une LIMITE (le candidat a pu la pratiquer autrement)
_DIRECT_ABSENCE = re.compile(r"\bjamais\s+(?:utilise|travaille|touche|pratique|manipule|use|worked|touched)|\b(?:pas|aucune?)\s+d'?\s*experience|\baucune?\s+(?:experience|pratique|connaissance)|"
                             r"\bne\s+(?:connais|connait|maitrise|pratique)\s+pas\b|\bno\s+(?:prior\s+|hands-on\s+)?experience\b|\bnever\s+(?:used|worked|touched)\b|\bsans\s+experience\b|\bjamais\s+pratique")
_SPECIFIC_ACTIVITY = re.compile(r"\b(?:administr\w*|configur\w*|deploy\w*|deploi\w*|migr\w*|exploit\w*|optimis\w*|supervis\w*|install\w*|parametr\w*|automatis\w*|"
                                r"monitor\w*|tun(?:e|ing)\b|conc\w*|develop\w*|implement\w*|mis\s+en\s+place|manag\w*|operat\w*|built|designed|set\s+up)\b")
_CLIENT_SUBJECT = re.compile(r"\b(?:le\s+|la\s+)?client(?:e)?\s+(?:\w+\s+){0,2}?(?:veut|veulent|exige|exigent|demande|demandent|cherche|cherchent|attend|attendent|souhaite|souhaitent|impose|precise|precisent|insiste)\b|"
                             r"\b(?:the\s+)?client\s+(?:wants|requires|requests|needs|expects|insists)\b")
_NOT_A_SPEAKER = re.compile(r"\b(?:tjm|taux|disponibilite|dispo|preavis|localisation|mobilite|teletravail|salaire|remuneration|statut|contexte|resume|note|notes|remarques?|"
                            r"conclusion|a\s+verifier|questions?|prochaine?s?\s+etapes?|competences?|experience|projet|mission|date|lieu|duree|objet|sujet)\b")
_HEDGE = re.compile(r"je\s+crois|peut-?etre|il\s+me\s+semble|\benviron\b|a\s+peu\s+pres|plus\s+ou\s+moins|de\s+memoire|je\s+pense|je\s+ne\s+sais\s+plus|sans\s+doute|probablement|si\s+je\s+me\s+souviens")
_WISH = re.compile(r"je\s+voudrais|je\s+souhaite|j'aimerais|je\s+cherche|je\s+vais\b|j'envisage|je\s+compte|je\s+recherche")
_CLAUSE = re.compile(r"[,;]|\bmais\b|\bet\b|\bpar\s+contre\b|\bsauf\b|\bcependant\b")


@dataclass
class Utterance:
    speaker: str
    role: str
    text: str
    start: int
    end: int


@dataclass
class Fact:
    kind: str                         # candidate_experience | candidate_constraint | client_requirement | other
    speaker: str
    speaker_role: str
    statement: str
    start: int
    end: int
    topic_key: str | None = None      # ex. "skill:kafka"
    topic_label: str | None = None
    evidence_kind: EvidenceKind | None = None
    level: Level | None = None
    certainty: str = "certain"        # certain | hedged
    needs_verification: bool = True
    why_verify: str = ""
    requirement_hint: dict[str, Any] | None = None
    constraint: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["evidence_kind"] = self.evidence_kind.value if self.evidence_kind else None
        d["level"] = self.level.value if self.level else None
        return d


def _plausible_speaker(label: str, speaker_map: dict[str, str] | None = None) -> bool:
    """« Kafka : uniquement 2 topics » ou « TJM : 650 € » ne sont pas des prises de parole : l'étiquette ne doit être ni une compétence ni un champ de fiche."""
    lab = label.strip()
    if speaker_map and any(fold(k).strip() == fold(lab).strip() for k in speaker_map):
        return True
    f = fold(lab)
    if role_of_label(lab, speaker_map) != "unknown" or re.fullmatch(r"(?:speaker|locuteur|locutrice|intervenant\w*|interlocuteur|interlocutrice|participant)\s*\d*", f):
        return True
    if _NOT_A_SPEAKER.search(f) or lx.detect_skills(lab) or re.search(r"\d", lab):
        return False
    return True


def role_of_label(label: str, speaker_map: dict[str, str] | None = None) -> str:
    if speaker_map:
        for k, v in speaker_map.items():
            if fold(k).strip() == fold(label).strip():
                return v
    f = fold(label)
    for role, hints in _ROLE_HINTS.items():
        if any(re.search(rf"\b{re.escape(h)}\b", f) for h in hints):
            return role
    return "unknown"


def parse_utterances(text: str, speaker_map: dict[str, str] | None = None) -> list[Utterance]:
    out: list[Utterance] = []
    pos = 0
    cur: Utterance | None = None
    for raw in text.split("\n"):
        a, b = pos, pos + len(raw)
        pos = b + 1
        if not raw.strip():
            continue
        m = _SPEAKER.match(raw)
        if m and len(m.group(1).split()) <= 4 and _plausible_speaker(m.group(1), speaker_map):
            if cur:
                out.append(cur)
            cur = Utterance(speaker=m.group(1).strip(), role=role_of_label(m.group(1), speaker_map), text=m.group(2), start=a + m.start(2), end=b)
        elif cur:
            cur.text += " " + raw.strip()
            cur.end = b
        else:
            cur = Utterance(speaker="", role="unknown", text=raw.strip(), start=a, end=b)
    if cur:
        out.append(cur)
    return out


def _clauses(sentence: str) -> list[str]:
    return [c for c in _CLAUSE.split(sentence) if c and c.strip()]


def _topics(sentence: str) -> list[lx.Skill]:
    return lx.detect_skills(sentence, prune_nested=True)


def _constraints(f: str) -> list[dict[str, Any]]:
    """Toutes les contraintes établies dans une phrase (disponibilité, TJM, présence, refus…)."""
    out: list[dict[str, Any]] = []
    if re.search(r"immediatement disponible|disponible immediatement|dispo immediatement|\bdisponibilite\s*[:\-]?\s*immediate", f):
        out.append({"field": "availability", "value": "immediate"})
    elif m := re.search(r"\bdisponibilite\s*[:\-]\s*(?:a partir (?:du|de)\s+)?([\w/]+(?:\s+[\w/]+){0,2})", f):
        out.append({"field": "availability", "value": re.sub(r"\s+(?:mon|ma|mes|et|je|mais)\b.*$", "", m.group(1).strip())})
    elif m := re.search(r"(?:disponible|dispo)\s+(?:a partir (?:du|de)|des|le|en)\s+([\w/]+(?:\s+[\w/]+){0,2})", f):
        out.append({"field": "availability", "value": re.sub(r"\s+(?:mon|ma|mes|et|je|mais)\b.*$", "", m.group(1).strip())})
    if m := re.search(r"preavis\s+(?:de\s+)?(\d+)\s*(mois|semaines?)", f):
        out.append({"field": "availability", "value": f"préavis {m.group(1)} {m.group(2)}"})
    if m := re.search(r"(?:tjm|taux journalier)\s*(?:est\s*)?(?:de|:|a|autour de|vers|cible)?\s*(\d{3,4})|(\d{3,4})\s*(?:€|euros?)\s*(?:/\s*j|par\s+jour|jour)", f):
        out.append({"field": "tjm", "value": int(m.group(1) or m.group(2))})
    if m := re.search(r"(?:maximum|max|pas plus de|jusqu'a)\s*(\d)\s*jours?\s*(?:de\s+)?(?:sur site|presentiel|sur place)", f):
        out.append({"field": "onsite_max_days", "value": int(m.group(1))})
    elif (m := re.search(r"(\d)\s*jours?\s*(?:par semaine\s*)?(?:de\s+)?(?:sur site|presentiel|sur place)", f)) and re.search(r"\b(?:ok|accepte|possible|pas de probleme|d'accord)\b", f):
        out.append({"field": "onsite_max_days", "value": int(m.group(1))})
    if re.search(r"refuse|pas question|ne veux pas|n'accepte pas|impossible pour moi|hors de question", f):
        for key, word in (("astreinte", r"astreintes?"), ("onsite", r"presentiel|sur site|sur place"), ("travel", r"deplacements?")):
            if re.search(word, f):
                out.append({"field": "refuses", "value": key})
    elif re.search(r"astreintes?", f) and re.search(r"\b(?:ok|accepte|pas de probleme|d'accord|sans souci)\b", f):
        out.append({"field": "accepts_astreinte", "value": True})
    return out


def extract_facts(text: str, kind: str = "transcript", *, speaker_map: dict[str, str] | None = None, auto_generated: bool = False) -> list[Fact]:
    if kind not in NOTE_KINDS:
        raise ValueError(f"type de source inconnu : {kind}")
    if kind == "transcript":
        utts = parse_utterances(text, speaker_map)
    else:
        default_role = {"candidate_call_note": "candidate_reported", "interview_report": "candidate_reported", "client_brief_note": "client_relayed",
                        "client_feedback": "client_relayed", "complementary_doc": "candidate_reported"}[kind]
        first = _SPEAKER.match(text.split("\n", 1)[0])
        utts = parse_utterances(text, speaker_map) if first and _plausible_speaker(first.group(1), speaker_map) else []
        if not utts:
            pos, utts = 0, []
            for raw in text.split("\n"):
                a, b = pos, pos + len(raw)
                pos = b + 1
                if raw.strip():
                    utts.append(Utterance(speaker="", role=default_role, text=raw.strip(), start=a, end=b))
        else:
            for u in utts:
                if u.role == "unknown":
                    u.role = default_role
    facts: list[Fact] = []
    for u in utts:
        base_off = u.start
        for s in sentences(u.text, 0):
            sent = s.text
            f = fold(sent)
            st, en = base_off + s.start, base_off + s.end
            if sent.strip().endswith("?"):
                continue
            auto_flag = auto_generated or kind == "transcript"
            role = u.role
            is_candidate = role in ("candidate", "candidate_reported")
            is_requirer = role in ("client", "recruiter", "sales", "client_relayed")
            explicit_client = bool(_CLIENT_SUBJECT.search(f))
            if (explicit_client or role == "unknown") and _REQ_CUE.search(f) or explicit_client:
                is_requirer = True              # « le client veut Kafka Connect » n'est JAMAIS une compétence du candidat, quel que soit le locuteur
            # --- exigence client (jamais une compétence du candidat)
            if is_requirer and (_REQ_CUE.search(f) or explicit_client):
                skills = _topics(sent)
                strong = bool(_REQ_STRONG.search(f)) and not _REQ_SOFT.search(f)
                cat = "imperatif" if strong else ("fortement_differenciant" if _REQ_DIFF.search(f) else ("souhaitable" if _REQ_SOFT.search(f) else "a_clarifier"))
                relayed = role != "client"                 # un locuteur « client » identifié est la seule source directe
                for sk in skills or [None]:
                    facts.append(Fact(
                        kind="client_requirement", speaker=u.speaker, speaker_role=role, statement=sent, start=st, end=en,
                        topic_key=(f"skill:{sk.key}" if sk and sk.kind in ("tech", "product") else (f"{'activity' if sk and sk.kind == 'activity' else 'domain'}:{sk.key}" if sk else None)),
                        topic_label=sk.label if sk else None, certainty="hedged" if _HEDGE.search(f) else "certain",
                        needs_verification=True,
                        why_verify=("Exigence relayée par un recruteur/commercial : à faire confirmer par le client avant de modifier la grille." if relayed
                                    else "Précision client à valider et à dater avant toute nouvelle version de grille."),
                        requirement_hint={"category_hint": cat, "relayed": relayed, "from_client": not relayed,
                                          "note": "Définit une exigence du poste, pas une compétence du candidat."}))
                continue
            if _WISH.search(f) and is_candidate:
                continue
            # --- contraintes du candidat
            if is_candidate or role == "unknown":
                cs = _constraints(f)
                for c in cs:
                    facts.append(Fact(kind="candidate_constraint", speaker=u.speaker, speaker_role=role, statement=sent, start=st, end=en,
                                      certainty="hedged" if _HEDGE.search(f) else "certain", needs_verification=auto_flag or bool(_HEDGE.search(f)),
                                      why_verify="Transcription automatique à vérifier." if auto_flag else ("Formulation hésitante." if _HEDGE.search(f) else ""),
                                      constraint=c))
                if cs:
                    continue
            if not (is_candidate or role == "unknown"):
                continue
            # --- expérience : une proposition à la fois (portée de la limite / contradiction)
            skills = _topics(sent)
            if not skills:
                continue
            fl = cl.flatten(sent)
            ff = fold(fl)
            neg = cl.negated_regions(ff)
            bounds = cl.clause_bounds(ff)
            for sk in skills:
                topic_key = f"{'skill' if sk.kind in ('tech', 'product', 'method') else sk.kind}:{sk.key}"
                hedged = bool(_HEDGE.search(f))
                hits = lx.mentions(sk, sent, ff)
                pos_hits = [h for h in hits if not any(a <= h[0] < b for a, b in neg)]
                neg_hits = [h for h in hits if any(a <= h[0] < b for a, b in neg)]
                if pos_hits:                         # une proposition AFFIRME : la négation voisine (autre composante) ne l'annule pas
                    h0 = pos_hits[0]
                    c0, c1 = cl.clause_of(bounds, h0[0])
                    clause = sent[c0:c1]
                    cf = ff[c0:c1]
                    if _LIMIT.search(cf):
                        ek, lvl = EvidenceKind.LIMITS, Level.PARTIAL
                    else:
                        sg = analyze_window(sent, sk, own_sentence=sent, mention_pos=h0[0], today=None, kind="activity" if sk.kind == "activity" else "skill")
                        lvl, _why = classify(sg, sk, "activity" if sk.kind == "activity" else ("domain" if sk.kind == "domain" else "skill"))
                        ek = EvidenceKind.SUPPORTS
                        if hedged and lvl == Level.CONFIRMED:
                            lvl = Level.PARTIAL
                elif neg_hits:                       # toutes les mentions sont niées : absence de la technologie, ou limite sur l'une de ses composantes
                    region = ff[min(a for a, _b in neg):max(b for _a, b in neg)]
                    component = any(re.match(rf"\s*(?:{'|'.join(re.escape(fold(t)) for t in (*sk.depth_terms, *sk.advanced_terms, *sk.narrowers, *sk.related))})\b", ff[h[1]:]) for h in neg_hits)
                    if _DIRECT_ABSENCE.search(region) and not component and not (_SPECIFIC_ACTIVITY.search(region) and not re.search(r"\bjamais\s+(?:utilise|travaille)", region)):
                        ek, lvl = EvidenceKind.CONTRADICTS, Level.CONTRADICTED
                    else:
                        ek, lvl = EvidenceKind.LIMITS, Level.PARTIAL
                else:
                    continue
                why = []
                if auto_flag:
                    why.append("transcription automatique : peut contenir des erreurs")
                if hedged:
                    why.append("formulation hésitante")
                if role == "unknown":
                    why.append("locuteur non identifié")
                facts.append(Fact(kind="candidate_experience", speaker=u.speaker, speaker_role=role, statement=sent, start=st, end=en,
                                  topic_key=topic_key, topic_label=sk.label, evidence_kind=ek, level=lvl,
                                  certainty="hedged" if hedged else "certain", needs_verification=bool(why), why_verify=" ; ".join(why).capitalize()))
    return facts


def reliability_for(kind: str) -> Reliability:
    return {"candidate_call_note": Reliability.EXPLAINED_IN_CALL, "transcript": Reliability.EXPLAINED_IN_CALL,
            "interview_report": Reliability.CONFIRMED_IN_INTERVIEW, "complementary_doc": Reliability.COMPLEMENTARY_DOC}.get(kind, Reliability.EXPLAINED_IN_CALL)


def source_for(kind: str) -> EvidenceSource:
    return {"candidate_call_note": EvidenceSource.CALL_NOTE, "transcript": EvidenceSource.TRANSCRIPT, "client_brief_note": EvidenceSource.CLIENT_BRIEF_NOTE,
            "interview_report": EvidenceSource.INTERVIEW_REPORT, "client_feedback": EvidenceSource.CLIENT_FEEDBACK,
            "complementary_doc": EvidenceSource.COMPLEMENTARY_DOC}[kind]


def candidate_facts_from(facts: list[Fact], base: Any | None = None):
    """Fusionne les contraintes établies (dernier fait gagnant) dans un CandidateFacts."""
    from .scoring import CandidateFacts
    cf = base or CandidateFacts()
    for f in facts:
        if f.kind != "candidate_constraint" or not f.constraint:
            continue
        fld, val = f.constraint["field"], f.constraint["value"]
        if fld == "refuses":
            cf.refuses.add(val)
        elif fld == "accepts_astreinte":
            cf.accepts_astreinte = bool(val)
        else:
            setattr(cf, fld, val)
    return cf
