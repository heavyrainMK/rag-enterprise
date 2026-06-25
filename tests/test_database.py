##############################################################
# Nom ......... : test_database.py
# Rôle ........ : Tests de la couche de persistance
#                 src/core/database.py (conversations + isolation).
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.0.0 du 19/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_database.py -v
# Dépendances . : pytest, sqlalchemy, src.core.database
##############################################################

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.database import (
    Base,
    Conversation,
    sauvegarder_conversation,
    lister_conversations,
    compter_conversations,
    supprimer_conversation,
)


@pytest.fixture
def db_session():
    moteur = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=moteur)
    Session = sessionmaker(bind=moteur)
    session = Session()
    yield session
    session.close()


class TestSauvegarde:

    def test_sauvegarde_simple(self, db_session):
        conv = sauvegarder_conversation(
            db=db_session,
            question="Combien de jours de congés ?",
            reponse="25 jours par an.",
            sources=["rh.pdf"],
        )
        assert conv.id is not None
        assert str(conv.question) == "Combien de jours de congés ?"
        assert str(conv.reponse) == "25 jours par an."

    def test_sources_serialisees(self, db_session):
        sources = ["doc1.pdf", "doc2.txt"]
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=sources,
        )
        assert conv.sources_en_liste() == sources

    def test_sources_vides(self, db_session):
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=[],
        )
        assert conv.sources_en_liste() == []

    def test_session_id(self, db_session):
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=[], session_id="session-abc",
        )
        assert str(conv.session_id) == "session-abc"

    def test_cree_le_automatique(self, db_session):
        conv = sauvegarder_conversation(db=db_session, question="Q", reponse="R", sources=[])
        assert conv.cree_le is not None


class TestLister:

    def test_liste_vide(self, db_session):
        assert lister_conversations(db_session) == []

    def test_toutes_les_conversations(self, db_session):
        for i in range(3):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert len(lister_conversations(db_session)) == 3

    def test_pagination(self, db_session):
        for i in range(10):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert len(lister_conversations(db_session, limite=3)) == 3

    def test_filtre_par_session(self, db_session):
        sauvegarder_conversation(db_session, "Q1", "R1", [], session_id="A")
        sauvegarder_conversation(db_session, "Q2", "R2", [], session_id="A")
        sauvegarder_conversation(db_session, "Q3", "R3", [], session_id="B")
        assert len(lister_conversations(db_session, session_id="A")) == 2
        assert len(lister_conversations(db_session, session_id="B")) == 1

    def test_tri_plus_recent(self, db_session):
        sauvegarder_conversation(db_session, "Première", "R1", [])
        c2 = sauvegarder_conversation(db_session, "Deuxième", "R2", [])
        result = lister_conversations(db_session)
        assert result[0].id == c2.id


class TestCompter:

    def test_compter_vide(self, db_session):
        assert compter_conversations(db_session) == 0

    def test_compter_apres_insertions(self, db_session):
        for i in range(5):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert compter_conversations(db_session) == 5


class TestSupprimer:

    def test_supprimer_existante(self, db_session):
        conv = sauvegarder_conversation(db_session, "Q", "R", [])
        assert supprimer_conversation(db_session, conv.id) is True
        assert compter_conversations(db_session) == 0

    def test_supprimer_inexistante(self, db_session):
        assert supprimer_conversation(db_session, 9999) is False

    def test_format_en_dict(self, db_session):
        conv = sauvegarder_conversation(db_session, "Question", "Réponse", ["source.pdf"], "sess-1")
        d = conv.en_dict()
        assert set(d.keys()) == {"id", "question", "reponse", "sources", "utilisateur", "session_id", "cree_le"}
        assert d["sources"] == ["source.pdf"]
        assert "T" in d["cree_le"]


class TestIsolationUtilisateur:

    def test_utilisateur_enregistre(self, db_session):
        conv = sauvegarder_conversation(db_session, "Q", "R", [], session_id="s", utilisateur="alice")
        assert str(conv.utilisateur) == "alice"
        assert conv.en_dict()["utilisateur"] == "alice"

    def test_lister_filtre_par_utilisateur(self, db_session):
        sauvegarder_conversation(db_session, "Qa", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qa2", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qb", "R", [], utilisateur="bob")
        assert len(lister_conversations(db_session, utilisateur="alice")) == 2
        assert len(lister_conversations(db_session, utilisateur="bob")) == 1

    def test_compter_filtre_par_utilisateur(self, db_session):
        sauvegarder_conversation(db_session, "Qa", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qb", "R", [], utilisateur="bob")
        assert compter_conversations(db_session, utilisateur="alice") == 1
        assert compter_conversations(db_session) == 2

    def test_isolation_avec_session_personnalisee(self, db_session):
        sauvegarder_conversation(db_session, "Q", "R", [], session_id="perso", utilisateur="alice")
        result = lister_conversations(db_session, utilisateur="alice")
        assert len(result) == 1
        assert str(result[0].session_id) == "perso"