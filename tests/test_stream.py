##############################################################
# Nom ......... : test_stream.py
# Rôle ........ : Tests de la route de streaming SSE
#                 src/api/routes/stream.py.
# Auteur ...... : Maxim Khomenko
# Version ..... : V1.0.0 du 19/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_stream.py -v
# Dépendances . : pytest, fastapi, httpx, unittest.mock
##############################################################

import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.auth import utilisateur_courant
from src.core.database import Utilisateur

client = TestClient(app)


def faux_user(nom="testuser"):
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = nom
    u.role = "user"
    u.actif = 1
    u.id = 1
    return u


def lire_sse(texte):
    evenements = []
    for ligne in texte.splitlines():
        if ligne.startswith("data: "):
            evenements.append(json.loads(ligne[len("data: "):]))
    return evenements


@pytest.fixture
def en_user():
    app.dependency_overrides[utilisateur_courant] = lambda: faux_user()
    yield
    app.dependency_overrides.clear()


class TestFormatEvenement:

    def test_ligne_sse_valide(self):
        from src.api.routes.stream import _evenement
        ligne = _evenement({"type": "token", "content": "salut"})
        assert ligne.startswith("data: ")
        assert ligne.endswith("\n\n")

    def test_accents_preserves(self):
        from src.api.routes.stream import _evenement
        ligne = _evenement({"type": "token", "content": "congés"})
        assert "congés" in ligne


class TestChatStream:

    def test_requiert_auth(self):
        assert client.post("/chat/stream", json={"question": "bonjour"}).status_code == 401

    def test_tokens_puis_done(self, en_user):
        def faux_stream(question, nom_utilisateur="anonyme"):
            yield {"type": "token", "content": "Bon"}
            yield {"type": "token", "content": "jour"}
            yield {"type": "done", "sources": ["doc.txt"],
                   "private_sources": [], "shared_sources": ["doc.txt"]}

        with patch("src.api.routes.stream.repondre_stream", side_effect=faux_stream), \
             patch("src.api.routes.stream.sauvegarder_conversation") as mock_save:
            r = client.post("/chat/stream", json={"question": "salut"})

        assert r.status_code == 200
        evenements = lire_sse(r.text)
        types = [e["type"] for e in evenements]
        assert types == ["token", "token", "done"]
        assert evenements[-1]["sources"] == ["doc.txt"]
        mock_save.assert_called_once()
        assert mock_save.call_args.kwargs["utilisateur"] == "testuser"
        assert mock_save.call_args.kwargs["reponse"] == "Bonjour"

    def test_evenement_sans_contexte(self, en_user):
        def faux_stream(question, nom_utilisateur="anonyme"):
            yield {"type": "no_context", "content": "Je ne trouve pas."}

        with patch("src.api.routes.stream.repondre_stream", side_effect=faux_stream), \
             patch("src.api.routes.stream.sauvegarder_conversation"):
            r = client.post("/chat/stream", json={"question": "hors sujet"})
        assert any(e["type"] == "no_context" for e in lire_sse(r.text))

    def test_evenement_erreur_arrete_stream(self, en_user):
        def faux_stream(question, nom_utilisateur="anonyme"):
            yield {"type": "token", "content": "début"}
            yield {"type": "error", "content": "LLM indisponible"}

        with patch("src.api.routes.stream.repondre_stream", side_effect=faux_stream), \
             patch("src.api.routes.stream.sauvegarder_conversation"):
            r = client.post("/chat/stream", json={"question": "q"})
        evenements = lire_sse(r.text)
        assert evenements[-1]["type"] == "error"
        assert "indisponible" in evenements[-1]["content"]