##############################################################
# Nom ......... : test_database.py
# Rôle ........ : Tests de la couche de persistance
#                 src/core/database.py (conversations + isolation).
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 27/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_database.py -v
# Dépendances . : pytest, sqlalchemy, src.core.database
##############################################################

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.database import (
    Base,
    sauvegarder_conversation,
    lister_conversations,
    compter_conversations,
    supprimer_conversation,
)


@pytest.fixture
def db_session():
    # Même dispositif que pour test_auth : base SQLite en mémoire, recréée à
    # neuf pour chaque test. Chaque test part donc d'une base vide et n'influence
    # pas les autres, ce qui rend les comptages (0, 2, 5...) fiables et
    # indépendants de l'ordre d'exécution.
    moteur = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=moteur)
    Session = sessionmaker(bind=moteur)
    session = Session()
    yield session
    session.close()


class TestSauvegarde:

    def test_sauvegarde_simple(self, db_session):
        # Cas nominal : après sauvegarde, l'objet a reçu un id (généré par la
        # base) et a bien conservé question et réponse. C'est la brique de base
        # sur laquelle reposent tous les autres tests de ce fichier.
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
        # Vérifie l'aller-retour de sérialisation : les sources sont stockées en
        # JSON dans une colonne texte (SQLite n'a pas de type liste), puis
        # relues. La liste ressortie doit être STRICTEMENT identique à l'entrée.
        sources = ["doc1.pdf", "doc2.txt"]
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=sources,
        )
        assert conv.sources_en_liste() == sources

    def test_sources_vides(self, db_session):
        # Cas limite : une conversation sans source doit donner une liste vide,
        # pas None ni une erreur. Important car une réponse de repli (aucun
        # contexte) est justement archivée sans source.
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=[],
        )
        assert conv.sources_en_liste() == []

    def test_session_id(self, db_session):
        # Le session_id fourni est bien persisté : c'est lui qui permettra
        # ensuite de regrouper et filtrer l'historique par session.
        conv = sauvegarder_conversation(
            db=db_session, question="Q", reponse="R", sources=[], session_id="session-abc",
        )
        assert str(conv.session_id) == "session-abc"

    def test_cree_le_automatique(self, db_session):
        # On ne fournit aucune date : elle doit être posée automatiquement à
        # l'insertion. Ce test protège le comportement du default=lambda de la
        # colonne (horodatage calculé au moment de chaque insertion).
        conv = sauvegarder_conversation(db=db_session, question="Q", reponse="R", sources=[])
        assert conv.cree_le is not None


class TestLister:

    def test_liste_vide(self, db_session):
        # Sur une base vierge, lister ne doit rien renvoyer (liste vide, pas
        # d'erreur). Cas de départ indispensable avant de tester les insertions.
        assert lister_conversations(db_session) == []

    def test_toutes_les_conversations(self, db_session):
        # Trois insertions, trois résultats : la lecture reflète fidèlement ce
        # qui a été écrit, sans perte ni doublon.
        for i in range(3):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert len(lister_conversations(db_session)) == 3

    def test_pagination(self, db_session):
        # Dix conversations en base, mais une limite à 3 : on ne doit récupérer
        # que 3 résultats. C'est le mécanisme qui évite de tout charger d'un coup
        # et qui permet l'affichage page par page côté client.
        for i in range(10):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert len(lister_conversations(db_session, limite=3)) == 3

    def test_filtre_par_session(self, db_session):
        # Trois conversations réparties sur deux sessions : filtrer par session
        # ne doit ramener que les conversations de cette session. On valide ainsi
        # que le filtre cloisonne correctement les échanges.
        sauvegarder_conversation(db_session, "Q1", "R1", [], session_id="A")
        sauvegarder_conversation(db_session, "Q2", "R2", [], session_id="A")
        sauvegarder_conversation(db_session, "Q3", "R3", [], session_id="B")
        assert len(lister_conversations(db_session, session_id="A")) == 2
        assert len(lister_conversations(db_session, session_id="B")) == 1

    def test_tri_plus_recent(self, db_session):
        # L'ordre de retour doit être « du plus récent au plus ancien ». On
        # insère deux conversations et on vérifie que la SECONDE arrive en tête.
        # C'est le comportement attendu d'un historique, et il dépend du
        # order_by(desc(cree_le)) de lister_conversations.
        sauvegarder_conversation(db_session, "Première", "R1", [])
        c2 = sauvegarder_conversation(db_session, "Deuxième", "R2", [])
        result = lister_conversations(db_session)
        assert result[0].id == c2.id


class TestCompter:

    def test_compter_vide(self, db_session):
        # Base vide : le total est 0. Référence de départ pour le test suivant.
        assert compter_conversations(db_session) == 0

    def test_compter_apres_insertions(self, db_session):
        # Le compteur doit suivre les insertions. Ce total est ce qui alimente
        # la pagination côté client (nombre de pages) : il doit donc être exact.
        for i in range(5):
            sauvegarder_conversation(db_session, f"Q{i}", f"R{i}", [])
        assert compter_conversations(db_session) == 5


class TestSupprimer:

    def test_supprimer_existante(self, db_session):
        # Suppression nominale : la fonction renvoie True ET la base ne contient
        # plus rien après coup. On vérifie les deux (le retour et l'effet réel).
        conv = sauvegarder_conversation(db_session, "Q", "R", [])
        assert supprimer_conversation(db_session, conv.id) is True
        assert compter_conversations(db_session) == 0

    def test_supprimer_inexistante(self, db_session):
        # Supprimer un id absent renvoie False sans lever d'erreur : c'est le
        # contrat sur lequel s'appuie la route (qui décide quoi faire de ce
        # False). On confirme que l'absence est gérée proprement, pas en plantant.
        assert supprimer_conversation(db_session, 9999) is False

    def test_format_en_dict(self, db_session):
        # Contrat de sérialisation : en_dict doit exposer EXACTEMENT cet ensemble
        # de clés, restituer les sources sous forme de liste, et formater la date
        # en ISO 8601 (le « T » sépare date et heure). Vérifier l'ensemble exact
        # des clés détecte tout champ ajouté ou retiré par inadvertance.
        conv = sauvegarder_conversation(db_session, "Question", "Réponse", ["source.pdf"], "sess-1")
        d = conv.en_dict()
        assert set(d.keys()) == {"id", "question", "reponse", "sources", "utilisateur", "session_id", "cree_le"}
        assert d["sources"] == ["source.pdf"]
        assert "T" in d["cree_le"]


class TestIsolationUtilisateur:
    # Cette classe est la plus importante du fichier : elle prouve, au niveau de
    # la persistance, l'isolation par utilisateur annoncée comme garantie de
    # sécurité du projet. Chaque test vérifie qu'un utilisateur ne voit et ne
    # compte que ses propres conversations.

    def test_utilisateur_enregistre(self, db_session):
        # Le propriétaire est bien stocké et ressort à l'identique, y compris
        # dans la sérialisation en_dict. C'est le préalable à tout filtrage.
        conv = sauvegarder_conversation(db_session, "Q", "R", [], session_id="s", utilisateur="alice")
        assert str(conv.utilisateur) == "alice"
        assert conv.en_dict()["utilisateur"] == "alice"

    def test_lister_filtre_par_utilisateur(self, db_session):
        # Cœur de l'isolation en lecture : deux conversations pour alice, une
        # pour bob. Filtrer par alice ne doit JAMAIS faire apparaître celle de
        # bob, et inversement. C'est exactement la garantie qu'attendent les
        # routes d'historique.
        sauvegarder_conversation(db_session, "Qa", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qa2", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qb", "R", [], utilisateur="bob")
        assert len(lister_conversations(db_session, utilisateur="alice")) == 2
        assert len(lister_conversations(db_session, utilisateur="bob")) == 1

    def test_compter_filtre_par_utilisateur(self, db_session):
        # Le comptage doit respecter la MÊME isolation que la liste : alice
        # compte 1, alors que le total non filtré (vue admin) compte 2. C'est ce
        # qui garantit qu'un utilisateur standard ne déduit même pas l'existence
        # des conversations des autres via un total erroné.
        sauvegarder_conversation(db_session, "Qa", "R", [], utilisateur="alice")
        sauvegarder_conversation(db_session, "Qb", "R", [], utilisateur="bob")
        assert compter_conversations(db_session, utilisateur="alice") == 1
        assert compter_conversations(db_session) == 2

    def test_isolation_avec_session_personnalisee(self, db_session):
        # Combinaison des deux filtres : même avec un session_id personnalisé, le
        # filtrage par utilisateur reste cohérent et renvoie la bonne
        # conversation. On vérifie que les deux dimensions (propriétaire et
        # session) coexistent sans se contredire.
        sauvegarder_conversation(db_session, "Q", "R", [], session_id="perso", utilisateur="alice")
        result = lister_conversations(db_session, utilisateur="alice")
        assert len(result) == 1
        assert str(result[0].session_id) == "perso"