##############################################################
# Nom ......... : test_documents.py
# Rôle ........ : Tests des routes /documents (téléversement,
#                 liste, suppression, sécurité path traversal).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.0.0 du 19/06/2026
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

client = TestClient(app)


def faux_admin():
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = "testadmin"
    u.role = "admin"
    u.actif = 1
    u.id = 1
    return u


def faux_user():
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = "testuser"
    u.role = "user"
    u.actif = 1
    u.id = 2
    return u


@pytest.fixture
def en_admin():
    app.dependency_overrides[utilisateur_courant] = lambda: faux_admin()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def en_user():
    app.dependency_overrides[utilisateur_courant] = lambda: faux_user()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def contenu_txt():
    return b"Politique RH - Article 1 : Les employes ont 25 jours de conges."


class TestTeleversement:

    def test_televersement_requiert_auth(self, contenu_txt):
        r = client.post("/documents/upload", files={"file": ("doc.txt", contenu_txt, "text/plain")})
        assert r.status_code == 401

    def test_extension_invalide(self, en_admin, contenu_txt):
        r = client.post("/documents/upload", files={"file": ("image.png", contenu_txt, "image/png")})
        assert r.status_code == 400
        assert "non supportée" in r.json()["detail"]

    def test_televersement_txt_valide(self, tmp_path, en_admin, contenu_txt):
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
        dossier = tmp_path / "users" / "testuser"; dossier.mkdir(parents=True)
        with patch("src.api.routes.documents.dossier_utilisateur", return_value=dossier), \
             patch("src.api.routes.documents.lancer_ingestion"):
            r = client.post("/documents/upload?scope=private",
                            files={"file": ("rapport.txt", contenu_txt, "text/plain")})
        assert r.status_code == 200
        assert (dossier / "rapport.txt").exists()

    def test_fichier_trop_gros(self, en_admin):
        with patch("src.api.routes.documents.TAILLE_MAX_MO", 0):
            r = client.post("/documents/upload?scope=shared",
                            files={"file": ("big.txt", b"contenu", "text/plain")})
        assert r.status_code == 413

    def test_user_ne_peut_pas_partager(self, en_user, contenu_txt):
        r = client.post("/documents/upload?scope=shared",
                        files={"file": ("doc.txt", contenu_txt, "text/plain")})
        assert r.status_code == 403


class TestListe:

    def test_liste_requiert_auth(self):
        assert client.get("/documents/").status_code == 401

    def test_liste_vide(self, tmp_path, en_admin):
        partage = tmp_path / "shared"; partage.mkdir()
        dossier = tmp_path / "users" / "testadmin"; dossier.mkdir(parents=True)
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.dossier_utilisateur", return_value=dossier):
            r = client.get("/documents/")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_liste_avec_documents(self, tmp_path, en_admin):
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
        assert client.delete("/documents/shared/fantome.txt").status_code == 401

    def test_supprimer_fichier_existant(self, tmp_path, en_admin):
        partage = tmp_path / "shared"; partage.mkdir()
        cible = partage / "old_doc.txt"; cible.write_text("à supprimer")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex"):
            r = client.delete("/documents/shared/old_doc.txt")
        assert r.status_code == 200
        assert not cible.exists()

    def test_supprimer_fichier_inexistant(self, tmp_path, en_admin):
        partage = tmp_path / "shared"; partage.mkdir()
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage):
            r = client.delete("/documents/shared/fantome.txt")
        assert r.status_code == 404


class TestPathTraversal:

    def test_televersement_traversal_reste_dans_dossier(self, tmp_path, en_admin, contenu_txt):
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
        partage = tmp_path / "shared"; partage.mkdir()
        dehors = tmp_path / "important.txt"; dehors.write_text("ne pas supprimer")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex"):
            r = client.delete("/documents/shared/..%2F..%2Fimportant.txt")
        assert dehors.exists()
        assert r.status_code in (400, 404)

    def test_suppression_nettoie_index(self, tmp_path, en_admin):
        partage = tmp_path / "shared"; partage.mkdir()
        cible = partage / "doc.txt"; cible.write_text("contenu")
        with patch("src.api.routes.documents.DOSSIER_PARTAGE", partage), \
             patch("src.api.routes.documents.supprimer_de_lindex") as mock_purge:
            r = client.delete("/documents/shared/doc.txt")
        assert r.status_code == 200
        assert not cible.exists()
        mock_purge.assert_called_once()
        assert mock_purge.call_args.args[1] == "doc.txt"