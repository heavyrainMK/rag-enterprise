##############################################################
# Nom ......... : test_documents.py
# Rôle ........ : Tests des routes /documents (téléversement,
#                 liste, suppression, sécurité path traversal).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.1.0 du 27/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_documents.py -v
# Dépendances . : pytest, fastapi, httpx, unittest.mock
##############################################################

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.auth import utilisateur_courant
from src.core.database import Utilisateur

# TestClient envoie de vraies requêtes HTTP à l'application, en mémoire, sans
# lancer de serveur. On teste donc les routes de bout en bout (validation,
# codes de statut, corps JSON), au plus près du comportement réel de l'API.
client = TestClient(app)


def faux_admin():
    # MagicMock(spec=Utilisateur) imite un objet Utilisateur sans toucher la
    # base : on fabrique un faux compte admin avec juste les attributs que les
    # routes vont lire. spec= contraint le mock à la forme réelle d'Utilisateur,
    # ce qui évite de tester contre un objet qui n'existerait pas en vrai.
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = "testadmin"
    u.role = "admin"
    u.actif = 1
    u.id = 1
    return u


def faux_user():
    # Même principe, mais avec le rôle « user » : sert à vérifier qu'un compte
    # standard se voit refuser ce qui est réservé aux admins.
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = "testuser"
    u.role = "user"
    u.actif = 1
    u.id = 2
    return u


@pytest.fixture
def en_admin():
    # dependency_overrides est le mécanisme de FastAPI pour REMPLACER une
    # dépendance le temps des tests. Ici on court-circuite utilisateur_courant
    # (qui exigerait un vrai jeton JWT) pour qu'il renvoie directement notre faux
    # admin. On teste ainsi la logique des routes sans gérer l'authentification.
    app.dependency_overrides[utilisateur_courant] = lambda: faux_admin()
    yield
    # Nettoyage indispensable après le test : sans ce clear, l'override fuiterait
    # vers les tests suivants et fausserait leurs résultats.
    app.dependency_overrides.clear()


@pytest.fixture
def en_user():
    # Variante « connecté en utilisateur standard ».
    app.dependency_overrides[utilisateur_courant] = lambda: faux_user()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def contenu_txt():
    # Petit contenu binaire réutilisable comme « fichier » téléversé dans les
    # tests. En bytes car un upload manipule des octets, pas du texte.
    return b"Politique RH - Article 1 : Les employes ont 25 jours de conges."


class TestTeleversement:

    def test_televersement_requiert_auth(self, contenu_txt):
        # Sans fixture d'authentification : aucune dépendance n'est surchargée,
        # donc la vraie protection s'applique. La route doit répondre 401. C'est
        # la garantie qu'un anonyme ne peut rien téléverser.
        r = client.post("/documents/upload", files={"file": ("doc.txt", contenu_txt, "text/plain")})
        assert r.status_code == 401

    def test_extension_invalide(self, en_admin, contenu_txt):
        # Même authentifié en admin, un type de fichier non autorisé (.png) est
        # refusé en 400, avec un message explicite. On ne traite que ce qu'on
        # sait charger en toute sécurité (liste blanche d'extensions).
        r = client.post("/documents/upload", files={"file": ("image.png", contenu_txt, "image/png")})
        assert r.status_code == 400
        assert "non supportée" in r.json()["detail"]

    def test_televersement_txt_valide(self, tmp_path, en_admin, contenu_txt):
        # Cas nominal. tmp_path est un dossier temporaire propre fourni par
        # pytest : on y redirige DOSSIER_PARTAGE via patch pour ne pas écrire
        # dans le vrai dossier du projet. On patche aussi lancer_ingestion pour
        # NE PAS déclencher la vraie vectorisation (lente, hors sujet ici) : on
        # teste la route, pas le pipeline d'ingestion. On vérifie ensuite le code
        # 200 et que la réponse confirme bien le nom et le lancement d'ingestion.
        partage = tmp_path / "shared"; partage.mkdir()
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.lancer_ingestion"):
            r = client.post("/documents/upload?scope=shared",
                            files={"file": ("test_doc.txt", contenu_txt, "text/plain")})
        assert r.status_code == 200
        data = r.json()
        assert data["nom_fichier"] == "test_doc.txt"
        assert data["ingestion_lancee"] is True

    def test_televersement_prive(self, tmp_path, en_user, contenu_txt):
        # Un utilisateur standard téléverse dans SON espace privé : autorisé. On
        # redirige son dossier vers tmp_path et on vérifie, fait concret, que le
        # fichier a réellement été écrit à l'endroit attendu.
        dossier = tmp_path / "users" / "testuser"; dossier.mkdir(parents=True)
        with patch("src.api.routes.documents.dossier_utilisateur", return_value=dossier), \
             patch("src.api.routes.documents.lancer_ingestion"):
            r = client.post("/documents/upload?scope=private",
                            files={"file": ("rapport.txt", contenu_txt, "text/plain")})
        assert r.status_code == 200
        assert (dossier / "rapport.txt").exists()

    def test_fichier_trop_gros(self, en_admin):
        # On abaisse la limite de taille à 0 Mo via patch pour forcer le dépassement
        # sans avoir à fabriquer un énorme fichier. La route doit répondre 413
        # (Payload Too Large), le code dédié au fichier trop volumineux.
        with patch("src.api.routes.documents.TAILLE_MAX_MO", 0):
            r = client.post("/documents/upload?scope=shared",
                            files={"file": ("big.txt", b"contenu", "text/plain")})
        assert r.status_code == 413

    def test_user_ne_peut_pas_partager(self, en_user, contenu_txt):
        # Contrôle d'autorisation : un compte standard qui tente de déposer dans
        # l'espace PARTAGÉ (scope=shared) est refusé en 403. Déposer dans le
        # partagé est une action réservée aux admins.
        r = client.post("/documents/upload?scope=shared",
                        files={"file": ("doc.txt", contenu_txt, "text/plain")})
        assert r.status_code == 403


class TestListe:

    def test_liste_requiert_auth(self):
        # Comme pour l'upload : lister les documents exige d'être authentifié.
        assert client.get("/documents/").status_code == 401

    def test_liste_vide(self, tmp_path, en_admin):
        # Dossiers partagé et privé vides (redirigés vers tmp_path) : la liste
        # renvoie un total de 0, sans erreur. Cas de référence avant d'ajouter
        # des fichiers dans le test suivant.
        partage = tmp_path / "shared"; partage.mkdir()
        dossier = tmp_path / "users" / "testadmin"; dossier.mkdir(parents=True)
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.dossier_utilisateur", return_value=dossier):
            r = client.get("/documents/")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_liste_avec_documents(self, tmp_path, en_admin):
        # On dépose trois fichiers dont un .png. La liste doit compter les DEUX
        # documents valides (.txt, .pdf) et IGNORER le .png : seuls les types
        # gérés apparaissent. Ce test verrouille le filtrage par extension côté
        # listing, cohérent avec celui de l'upload.
        partage = tmp_path / "shared"; partage.mkdir()
        dossier = tmp_path / "users" / "testadmin"; dossier.mkdir(parents=True)
        (partage / "doc1.txt").write_text("contenu 1")
        (partage / "doc2.pdf").write_bytes(b"%PDF fake")
        (partage / "image.png").write_bytes(b"png data")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.dossier_utilisateur", return_value=dossier):
            r = client.get("/documents/")
        assert r.status_code == 200
        data = r.json()
        assert data["nb_partages"] == 2
        noms = {d["nom_fichier"] for d in data["documents"]}
        assert "doc1.txt" in noms and "doc2.pdf" in noms
        assert "image.png" not in noms


class TestSuppression:

    def test_suppression_requiert_auth(self):
        # La suppression aussi est protégée : un anonyme reçoit 401.
        assert client.delete("/documents/shared/fantome.txt").status_code == 401

    def test_supprimer_fichier_existant(self, tmp_path, en_admin):
        # Suppression nominale : le fichier existe, la route renvoie 200 et le
        # fichier a bien disparu du disque. On patche supprimer_de_lindex pour
        # isoler le test du nettoyage ChromaDB (testé séparément plus bas).
        partage = tmp_path / "shared"; partage.mkdir()
        cible = partage / "old_doc.txt"; cible.write_text("à supprimer")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex"):
            r = client.delete("/documents/shared/old_doc.txt")
        assert r.status_code == 200
        assert not cible.exists()

    def test_supprimer_fichier_inexistant(self, tmp_path, en_admin):
        # Supprimer un fichier absent renvoie 404, pas une erreur serveur. La
        # route distingue proprement « rien à supprimer » d'un vrai problème.
        partage = tmp_path / "shared"; partage.mkdir()
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage):
            r = client.delete("/documents/shared/fantome.txt")
        assert r.status_code == 404


class TestPathTraversal:
    # Classe de sécurité critique : le « path traversal » est l'attaque où un nom
    # de fichier piégé (avec des « ../ ») tente de sortir du dossier autorisé pour
    # lire ou écrire ailleurs sur le serveur. Ces tests prouvent que l'API neutralise
    # ces tentatives, à l'écriture comme à la suppression.

    def test_televersement_traversal_reste_dans_dossier(self, tmp_path, en_admin, contenu_txt):
        # On téléverse un fichier nommé « ../../secret.txt » pour tenter d'écrire
        # HORS du dossier partagé. Vérifications : le fichier « dehors » n'est
        # jamais créé, un seul fichier est écrit, et il l'est DANS le dossier
        # autorisé avec un nom nettoyé (plus de « .. »). Le nom est neutralisé,
        # l'écriture reste confinée.
        partage = tmp_path / "shared"; partage.mkdir()
        dehors = tmp_path / "secret.txt"
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.lancer_ingestion"):
            r = client.post("/documents/upload?scope=shared",
                            files={"file": ("../../secret.txt", contenu_txt, "text/plain")})
        assert r.status_code == 200
        assert not dehors.exists()
        ecrits = list(partage.iterdir())
        assert len(ecrits) == 1
        assert ".." not in ecrits[0].name

    def test_suppression_traversal_rejetee(self, tmp_path, en_admin):
        # Même attaque côté suppression, avec des « ../ » encodés en URL
        # (%2F = « / »), pour viser un fichier important hors du dossier. La
        # garantie ESSENTIELLE, vérifiée en premier, est que le fichier visé
        # SURVIT : l'attaque ne supprime rien hors du dossier autorisé.
        #
        # Côté code de statut, deux issues sont acceptables et toutes deux sûres :
        #  - 400/404 si la requête atteint la route, qui rejette le nom piégé ;
        #  - 405 si, après décodage, le serveur normalise « ../../important.txt »
        #    en « /important.txt » : la requête ne correspond alors plus à la
        #    route DELETE et est arrêtée en amont par le routeur (seule une route
        #    GET catch-all existe pour ce chemin, d'où « méthode non autorisée »).
        # Dans les deux cas la suppression n'a pas lieu ; on accepte donc ces
        # trois codes, l'assertion vraiment déterminante restant dehors.exists().
        partage = tmp_path / "shared"; partage.mkdir()
        dehors = tmp_path / "important.txt"; dehors.write_text("ne pas supprimer")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex"):
            r = client.delete("/documents/shared/..%2F..%2Fimportant.txt")
        assert dehors.exists()
        assert r.status_code in (400, 404, 405)

    def test_suppression_nettoie_index(self, tmp_path, en_admin):
        # Au-delà du fichier, supprimer un document doit AUSSI purger ses vecteurs
        # de l'index ChromaDB, sinon des « vecteurs orphelins » continueraient de
        # polluer la recherche. On utilise un mock pour vérifier que la purge est
        # appelée EXACTEMENT une fois, avec le bon nom de fichier en argument.
        # Ce test garde la trace du problème d'index désynchronisé rencontré
        # pendant le projet : la suppression nettoie désormais les deux côtés.
        partage = tmp_path / "shared"; partage.mkdir()
        cible = partage / "doc.txt"; cible.write_text("contenu")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex") as mock_purge:
            r = client.delete("/documents/shared/doc.txt")
        assert r.status_code == 200
        assert not cible.exists()
        mock_purge.assert_called_once()
        assert mock_purge.call_args.args[1] == "doc.txt"