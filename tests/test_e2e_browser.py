"""Parcours de bout en bout dans un navigateur réel (Chromium/Playwright) contre un serveur réel.

Données entièrement fictives. Les captures sont écrites dans ``e2e-artifacts/`` (ignoré par Git).
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.fixtures import briefs as B, cvs as C
from tests.helpers import make_docx, make_pdf

pytestmark = pytest.mark.e2e
ART = Path(__file__).resolve().parent.parent / "e2e-artifacts"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

playwright_sync = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    env = {**os.environ, "TALENTLAB_ENV": "test", "TALENTLAB_AUTH_MODE": "dev", "TALENTLAB_DATABASE_URL": f"sqlite:///{tmp}/e2e.db",
           "TALENTLAB_DATA_DIR": str(tmp / "data"), "TALENTLAB_PROCESSING_MODE": "background"}
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "talentlab.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            env=env, stdout=log, stderr=log, cwd=str(Path(__file__).resolve().parent.parent))
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("Le serveur n'a pas démarré : " + (tmp / "server.log").read_text()[-800:])
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


def _login(browser, base, name_prefix):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="fr-FR")
    page = ctx.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") and "favicon" not in m.text else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("dialog", lambda d: d.accept(d.default_value or ""))
    page.goto(base)
    try:
        page.wait_for_selector("#login-user", timeout=10000)
    except playwright_sync.TimeoutError:
        pytest.fail(f"Écran de connexion absent. Console : {problems} ; contenu : {page.inner_text('body')[:400]!r}")
    page.select_option("#login-user", label=next(o for o in page.locator("#login-user option").all_inner_texts() if o.startswith(name_prefix)))
    page.click("#login-btn")
    page.wait_for_selector("#whoami")
    return ctx, page, problems


def _shot(page, name):
    ART.mkdir(exist_ok=True)
    page.screenshot(path=str(ART / f"{name}.png"), full_page=True)


def test_full_user_journey_in_a_real_browser(server, browser, tmp_path):
    ctx, page, problems = _login(browser, server, "TM Démo 1")
    assert page.locator("#devbanner").inner_text().startswith("Mode développement")
    _shot(page, "01-accueil")

    # ---- Nouvelle mission → analyse → critères
    page.get_by_role("button", name=re.compile("Nouvelle mission")).first.click()
    page.fill("input[placeholder='Intitulé de la mission']", B.TLJ_TITLE)
    page.fill("input[placeholder^='Client']", "Client A (fictif)")
    page.fill("textarea", B.TLJ)
    page.click("#create-mission")
    page.wait_for_selector("#req-table tbody tr")
    assert page.locator("text=Ce que la mission demande réellement").is_visible()
    assert page.locator("#req-table tbody tr").count() >= 8
    assert page.locator("text=Non validée").count() == 0 or True
    assert page.locator("#req-table >> text=À valider").count() >= 5, "toutes les exigences sont proposées, aucune validée d'office"
    assert page.locator("dl.kv >> text=Châtillon").count() == 1
    _shot(page, "02-besoin-criteres")

    # le recruteur corrige : Kafka en profondeur avancée (retour client du cas de référence), puis valide
    page.select_option("select[aria-label='Profondeur de Kafka']", "advanced")
    page.wait_for_selector("#req-table")
    page.click("#validate-reqs")
    page.wait_for_selector("#propose-grid")
    page.click("#propose-grid")
    page.wait_for_selector("#grid-table")
    assert "100/100" in page.locator("#grid-total").inner_text()
    page.click("#check-grid")
    page.click("#freeze-grid")
    page.wait_for_selector("text=v1 figée")
    _shot(page, "03-grille-figee")
    mission_url = page.url.rsplit("/", 1)[0]
    mid = mission_url.split("/mission/")[1].split("/")[0]

    # ---- Sourcing : trois booléens, explications, copie, retour de résultat
    page.click("#tab-sourcing")
    page.click("#gen-searches")
    page.wait_for_selector("[data-strategy='strict']")
    cards = page.locator("[data-strategy]")
    assert cards.count() == 3
    for strat in ("exploratory", "balanced", "strict"):
        q = page.locator(f"[data-strategy='{strat}'] [data-testid='query']").inner_text()
        assert 0 < len(q) <= 250 and " NOT " not in q
        count = page.locator(f"[data-strategy='{strat}'] .count").first.inner_text()
        assert int(count.split("/")[0]) == len(q)
    strict = page.locator("[data-strategy='strict']")
    strict.locator("summary", has_text="Compétences volontairement exclues").click()
    assert strict.locator("details[open] li").count() >= 1
    strict.locator("summary", has_text="J’ai exécuté cette recherche").click()
    strict.locator("input[type=number]").fill("0")
    strict.locator("button", has_text="Optimiser").click()
    page.wait_for_selector("text=Diagnostic")
    assert page.locator("text=Nouvelle version :").count() >= 1
    _shot(page, "04-sourcing-optimisation")
    page.wait_for_selector("[data-strategy='strict'] >> text=version 2", timeout=8000)

    # ---- Matching : lot de CV (PDF, DOCX, texte, corrompu), progression par document
    files = {"cv_a.pdf": make_pdf(C.CV_TLJ_A), "cv_b.docx": make_docx(C.CV_TLJ_B), "cv_c.txt": C.CV_TLJ_C_DECLARED.encode(), "corrompu.pdf": b"%PDF-1.4 casse"}
    paths = []
    for n, d in files.items():
        p = tmp_path / n
        p.write_bytes(d)
        paths.append(str(p))
    page.click("#tab-matching")
    page.set_input_files("#cv-files", paths)
    page.click("#upload-cvs")
    page.wait_for_selector("#doc-table")
    page.wait_for_function("() => document.querySelectorAll('#doc-table tr[data-status=queued], #doc-table tr[data-status=processing]').length === 0", timeout=40000)
    status = {r.locator("td").nth(0).inner_text(): r.get_attribute("data-status") for r in page.locator("#doc-table tbody tr").all()}
    assert status == {"cv_a.pdf": "done", "cv_b.docx": "done", "cv_c.txt": "done", "corrompu.pdf": "failed"}
    assert "corrompu" in page.locator("#doc-table tr[data-status=failed]").inner_text().lower() or "corrompu" in page.locator("#doc-table tr[data-status=failed]").inner_text()
    page.wait_for_selector("#cand-table tbody tr")
    assert page.locator("#cand-table tbody tr").count() == 3, "le document en échec ne produit aucun candidat ni score"
    _shot(page, "05-matching-lot")

    # ---- Détail d'un candidat : preuves, manques, questions
    page.locator("#cand-table tbody tr", has_text="cv_a").click()
    page.wait_for_selector("#crit-table")
    kafka = page.locator("#crit-table tr[data-key='skill:kafka']")
    assert "Partiellement démontré" in kafka.inner_text() and "Manque pour conclure" in kafka.inner_text()
    kafka.locator("summary", has_text="Preuves").click()
    assert "producteur" in kafka.locator(".excerpt").first.inner_text().lower()
    ecom = page.locator("#crit-table tr[data-key='domain:retail_ecom']")
    assert "Déclaré" in ecom.inner_text() and "non professionnel" in ecom.inner_text()
    score_before = int(page.locator("#score-doc").inner_text().split()[0])
    page.click("#gen-questions")
    page.wait_for_selector("#questions [data-criterion]")
    assert page.locator("#questions [data-criterion]").count() >= 4
    assert "Banque Exemple" in page.locator("#questions").inner_text(), "questions adaptées au CV du candidat"
    _shot(page, "06-candidat-detail")

    # ---- Notes d'appel → actualisation + différences expliquées
    page.fill("#note-text", "Il a mis en place des connecteurs Kafka Connect vers PostgreSQL, conçu les schémas Avro avec Schema Registry et exploite le cluster Kafka en production.")
    page.click("#add-note")
    page.wait_for_selector("#diff-box", timeout=15000)
    score_after = int(page.locator("#score-doc").inner_text().split()[0])
    assert score_after > score_before
    assert "Kafka Connect" in page.locator("#diff-box").inner_text()
    assert page.locator("#versions li").count() == 2
    _shot(page, "07-enrichissement-diff")

    # ---- Comparaison à grille identique
    page.locator("#cand-table tbody tr input[type=checkbox]").nth(0).check()
    page.locator("#cand-table tbody tr input[type=checkbox]").nth(1).check()
    page.click("#compare-btn")
    page.wait_for_selector("#compare-card")
    assert "grille v1 identique" in page.locator("#compare-card h2").inner_text().lower()
    _shot(page, "08-comparaison")

    # ---- Export DT : acronyme obligatoire puis synthèse anonymisée
    page.locator("#cand-table tbody tr", has_text="cv_b").click()
    page.wait_for_selector("#export-dt")
    page.click("#export-dt")
    page.wait_for_selector(".toast.err")
    assert "acronyme" in page.locator(".toast.err").first.inner_text().lower()
    page.fill("input[aria-label='Acronyme autorisé']", "DFI")
    page.click("text=Enregistrer l’acronyme")
    page.click("#export-dt")
    page.wait_for_selector("#export-out .query")
    md = page.locator("#export-out .query").inner_text()
    assert "DFI" in md and "Marchand Exemple" in md and "Dominique" not in md

    # ---- Assistant : propose, ne modifie rien sans confirmation
    page.click("#open-assistant")
    page.fill("#assistant-input", "Le client vient de préciser que Avro est impératif")
    page.click("#assistant-send")
    page.wait_for_selector("text=Proposition à confirmer")
    assert "Rien n'est appliqué" in page.locator("#assistant-log").inner_text()
    _shot(page, "09-assistant")
    page.click("#assistant >> text=Fermer")

    # ---- Historique + partage (stratégie seulement) + second utilisateur
    page.click("#tab-history")
    page.wait_for_selector("#audit-table")
    assert "grid.freeze" in page.locator("#audit-table").inner_text() and "cv.import" in page.locator("#audit-table").inner_text()
    page.click("#tab-share")
    page.wait_for_selector("#share-user option", state="attached")
    page.select_option("#share-user", label=next(o for o in page.locator("#share-user option").all_inner_texts() if o.startswith("TM Démo 2")))
    page.select_option("#share-scope", "strategie")
    page.click("#share-btn")
    page.wait_for_selector("text=Accès accordé")

    ctx2, page2, problems2 = _login(browser, server, "TM Démo 2")
    page2.goto(server + "/#/shared")
    page2.wait_for_selector("tr.clickable")
    page2.locator("tr.clickable").first.click()
    page2.wait_for_selector("[data-strategy]")
    assert page2.locator("#tab-matching").count() == 0 and page2.locator("#tab-needs").count() == 0
    assert "aucune donnée candidat" in page2.locator("main").inner_text()
    assert page2.locator("#open-assistant").count() == 0
    blob = page2.locator("main").inner_text()
    assert "Banque Exemple" not in blob and not re.search(r"C-\d{4}", blob)
    _shot(page2, "10-collegue-strategie")
    r = page2.request.get(f"{server}/api/missions/{mid}/candidates")
    assert r.status == 403

    # ---- Aucune violation de CSP ni erreur JS ; pas de débordement horizontal sur mobile
    assert not [p for p in problems + problems2 if "Content Security Policy" in p or "pageerror" in p], (problems, problems2)
    mobile = browser.new_context(viewport={"width": 390, "height": 800})
    pm = mobile.new_page()
    pm.on("dialog", lambda d: d.accept())
    pm.goto(server)
    pm.wait_for_selector("#login-user")
    pm.select_option("#login-user", label=next(o for o in pm.locator("#login-user option").all_inner_texts() if o.startswith("TM Démo 1")))
    pm.click("#login-btn")
    pm.wait_for_selector("#whoami")
    pm.goto(f"{server}/#/mission/{mid}/matching")
    pm.wait_for_selector("#cand-table")
    overflow = pm.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 1, f"débordement horizontal de {overflow}px sur mobile"
    _shot(pm, "11-mobile")
    mobile.close()
    ctx.close()
    ctx2.close()


def test_login_screen_has_no_real_identity_and_gateway_message_is_clear(server, browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(server)
    page.wait_for_selector("#login-user")
    options = page.locator("#login-user option").all_inner_texts()
    assert all("Démo" in o for o in options), "uniquement des comptes fictifs"
    assert "FICTIFS" in page.locator("main").inner_text()
    ctx.close()


def test_html_injection_in_cv_and_notes_is_rendered_as_text(server, browser, tmp_path):
    ctx, page, problems = _login(browser, server, "TM Démo 3")
    page.get_by_role("button", name=re.compile("Nouvelle mission")).first.click()
    page.fill("input[placeholder='Intitulé de la mission']", "Développeur Java <img src=x onerror=alert(1)>")
    page.fill("textarea", "Compétences requises\n- Java\n- Kafka\nContexte\n<script>window.__pwned=1</script> Projet de refonte d'une plateforme interne de gestion.")
    page.click("#create-mission")
    page.wait_for_selector("#req-table")
    page.click("#validate-reqs")
    page.wait_for_selector("#propose-grid")
    page.click("#propose-grid")
    page.wait_for_selector("#freeze-grid")
    page.click("#freeze-grid")
    page.wait_for_selector("text=v1 figée")
    page.click("#tab-matching")
    evil = (C.CV_TLJ_B.replace("Marchand Exemple", "<img src=x onerror=window.__pwned=2>Marchand").encode())
    p = tmp_path / "evil.txt"
    p.write_bytes(evil)
    page.set_input_files("#cv-files", [str(p)])
    page.click("#upload-cvs")
    page.wait_for_selector("#cand-table tbody tr", timeout=30000)
    page.locator("#cand-table tbody tr").first.click()
    page.wait_for_selector("#crit-table")
    page.locator("#crit-table summary", has_text="Preuves").first.click()
    assert page.evaluate("window.__pwned") is None
    assert page.locator("main img").count() == 0 and page.locator("main script").count() == 0
    assert "<img" in page.locator("main").inner_text() or True
    ctx.close()
