##############################################################
# Nom ......... : test_stream.py
# Rôle ........ : Tests de la route de streaming SSE
#                 src/api/routes/stream.py.
# Auteur ...... : Maxim Khomenko
# Version ..... : V1.1.0 du 27/06/2026
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
    # Faux compte standard, comme dans test_documents : on imite un Utilisateur
    # sans base, pour court-circuiter l'authentification dans les tests de route.
    u = MagicMock(spec=Utilisateur)
    u.nom_utilisateur = nom
    u.role = "user"
    u.actif = 1
    u.id = 1
    return u


def lire_sse(texte):
    # Petit parseur SSE pour les tests : la réponse de streaming est un flux
    # texte où chaque événement est une ligne « data: <json> ». On reconstruit
    # donc la liste des événements en isolant ces lignes et en décodant leur
    # charge JSON. Cela nous permet ensuite de raisonner sur des dictionnaires
    # Python plutôt que sur du texte brut. C'est le pendant, côté test, de la
    # mise en forme faite par _evenement dans stream.py.
    evenements = []
    for ligne in texte.splitlines():
        if ligne.startswith("data: "):
            evenements.append(json.loads(ligne[len("data: "):]))
    return evenements


@pytest.fixture
def en_user():
    # Override de la dépendance d'authentification, avec nettoyage après le test
    # (sinon l'override fuiterait vers les tests suivants).
    app.dependency_overrides[utilisateur_courant] = lambda: faux_user()
    yield
    app.dependency_overrides.clear()


class TestFormatEvenement:

    def test_ligne_sse_valide(self):
        # On teste directement la fonction de mise en forme _evenement : sa sortie
        # doit commencer par « data: » et finir par une ligne vide (le double saut
        # de ligne). Ce format n'est pas cosmétique : c'est lui qui délimite les
        # événements pour le client SSE. Une ligne mal formée casserait le
        # découpage côté navigateur.
        from src.api.routes.stream import _evenement
        ligne = _evenement({"type": "token", "content": "salut"})
        assert ligne.startswith("data: ")
        assert ligne.endswith("\n\n")

    def test_accents_preserves(self):
        # Les accents doivent traverser la sérialisation intacts (« congés » et
        # non « cong\u00e9s »). C'est le rôle du ensure_ascii=False dans
        # _evenement ; ce test le verrouille, car un corpus francophone en dépend.
        from src.api.routes.stream import _evenement
        ligne = _evenement({"type": "token", "content": "congés"})
        assert "congés" in ligne


class TestChatStream:

    def test_requiert_auth(self):
        # Sans authentification, la route de streaming renvoie 401 : on ne
        # streame une réponse qu'à un utilisateur connecté.
        assert client.post("/chat/stream", json={"question": "bonjour"}).status_code == 401

    def test_tokens_puis_done(self, en_user):
        # Cas nominal de bout en bout. On remplace le moteur repondre_stream par
        # un faux générateur qui émet deux tokens puis un « done ». On vérifie
        # alors plusieurs garanties d'un coup :
        #  - la séquence d'événements reçue est bien token, token, done ;
        #  - l'événement final porte les sources ;
        #  - la sauvegarde en historique est appelée UNE fois, avec le bon
        #    propriétaire, et avec la réponse RECONSTITUÉE (« Bon » + « jour » =
        #    « Bonjour »). Ce dernier point prouve que la route recolle bien les
        #    tokens pour archiver la réponse complète, alors que le client, lui,
        #    les a reçus séparément.
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
        # Quand le moteur signale l'absence de contexte, la route doit relayer un
        # événement no_context au client. C'est la version « vue de la route » du
        # garde-fou anti-hallucination : l'utilisateur reçoit un message honnête.
        def faux_stream(question, nom_utilisateur="anonyme"):
            yield {"type": "no_context", "content": "Je ne trouve pas."}

        with patch("src.api.routes.stream.repondre_stream", side_effect=faux_stream), \
             patch("src.api.routes.stream.sauvegarder_conversation"):
            r = client.post("/chat/stream", json={"question": "hors sujet"})
        assert any(e["type"] == "no_context" for e in lire_sse(r.text))

    def test_evenement_erreur_arrete_stream(self, en_user):
        # Si le moteur émet une erreur en cours de route, le flux doit se TERMINER
        # sur cet événement d'erreur (et ne rien relayer après). On vérifie que le
        # dernier événement est bien de type error et qu'il transporte le message.
        # C'est ce qui permet au client d'afficher une erreur claire plutôt que de
        # rester en attente d'un flux qui ne reprendra pas.
        def faux_stream(question, nom_utilisateur="anonyme"):
            yield {"type": "token", "content": "début"}
            yield {"type": "error", "content": "LLM indisponible"}

        with patch("src.api.routes.stream.repondre_stream", side_effect=faux_stream), \
             patch("src.api.routes.stream.sauvegarder_conversation"):
            r = client.post("/chat/stream", json={"question": "q"})
        evenements = lire_sse(r.text)
        assert evenements[-1]["type"] == "error"
        assert "indisponible" in evenements[-1]["content"]