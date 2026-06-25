##############################################################
# Nom ......... : database.py
# Rôle ........ : Couche de persistance SQLite pour l'API RAG
#                 Enterprise. Définit les modèles SQLAlchemy
#                 (Conversation, Utilisateur), initialise la base
#                 et fournit les opérations de lecture/écriture
#                 pour l'historique et les comptes.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 23/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py et les routes.
#                 La base rag_history.db est créée automatiquement
#                 au premier démarrage via creer_base().
# Dépendances . : sqlalchemy, bibliothèque standard (json,
#                 logging, datetime)
##############################################################

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    desc,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHEMIN_BASE: Path = Path("rag_history.db")
URL_BASE: str = f"sqlite:///{CHEMIN_BASE}"

moteur = create_engine(
    URL_BASE,
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocale = sessionmaker(autocommit=False, autoflush=False, bind=moteur)


# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Conversation(Base):
    """
    Représente un échange question/réponse dans l'historique.

    Attributs
    ---------
    id : int
        Identifiant auto-incrémenté.
    question : str
        Question posée par l'utilisateur.
    reponse : str
        Réponse générée par le modèle.
    sources : str
        Liste des sources (noms de fichiers) sérialisée en JSON.
    utilisateur : str
        Propriétaire de la conversation (isolation par utilisateur).
    session_id : str
        Identifiant de session (pour regrouper les échanges).
    cree_le : datetime
        Horodatage UTC de l'échange.
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    reponse = Column(Text, nullable=False)
    sources = Column(String(1000), default="[]")
    utilisateur = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    cree_le = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def sources_en_liste(self):
        """Transforme le champ sources (JSON) en liste Python."""
        try:
            brut = str(self.sources) if self.sources is not None else "[]"
            return json.loads(brut)
        except json.JSONDecodeError:
            return []

    def en_dict(self):
        """Transforme l'objet en dictionnaire pour l'API."""
        return {
            "id": self.id,
            "question": self.question,
            "reponse": self.reponse,
            "sources": self.sources_en_liste(),
            "utilisateur": self.utilisateur,
            "session_id": self.session_id,
            "cree_le": self.cree_le.isoformat(),
        }


class Utilisateur(Base):
    """
    Représente un utilisateur de l'application.

    Attributs
    ---------
    id : int
        Identifiant auto-incrémenté.
    nom_utilisateur : str
        Nom d'utilisateur unique.
    mot_de_passe_hache : str
        Mot de passe haché avec bcrypt.
    role : str
        Rôle : 'user' ou 'admin'.
    actif : int
        Compte actif (1) ou désactivé (0). SQLite n'a pas de booléen natif.
    cree_le : datetime
        Date de création du compte.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom_utilisateur = Column(String(100), unique=True, nullable=False, index=True)
    mot_de_passe_hache = Column(String(200), nullable=False)
    role = Column(String(20), default="user", nullable=False)
    actif = Column(Integer, default=1)
    cree_le = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def en_dict(self):
        """Transforme l'objet en dictionnaire pour l'API."""
        return {
            "id": self.id,
            "nom_utilisateur": self.nom_utilisateur,
            "role": self.role,
            "actif": bool(self.actif),
            "cree_le": self.cree_le.isoformat(),
        }


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def creer_base():
    """Crée les tables si elles n'existent pas encore."""
    Base.metadata.create_all(bind=moteur)
    ajouter_colonne_utilisateur()
    logger.info("Base de données initialisée : %s", CHEMIN_BASE)


def ajouter_colonne_utilisateur():
    """
    Ajoute la colonne 'utilisateur' à la table conversations si elle manque.

    Migration ponctuelle, utile pour les bases créées avant l'isolation
    par utilisateur. Sans effet (early return) sur une base à jour.
    Les lignes existantes auront utilisateur = NULL.
    """
    from sqlalchemy import inspect, text

    inspecteur = inspect(moteur)
    if "conversations" not in inspecteur.get_table_names():
        return
    colonnes = {col["name"] for col in inspecteur.get_columns("conversations")}
    if "utilisateur" in colonnes:
        return
    with moteur.begin() as connexion:
        connexion.execute(text("ALTER TABLE conversations ADD COLUMN utilisateur VARCHAR(100)"))
    logger.info("Migration : colonne 'utilisateur' ajoutée à 'conversations'.")


def get_db():
    """
    Fournit une session de base de données pour FastAPI.
    La session est fermée automatiquement après usage.
    """
    db = SessionLocale()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Opérations sur l'historique
# ---------------------------------------------------------------------------
def appliquer_filtres(requete, session_id=None, utilisateur=None, recherche=None):
    """
    Applique les filtres communs à une requête sur les conversations :
    propriétaire, session et mot-clé dans la question.

    Centralisé ici pour que lister, compter et vider restent toujours
    synchronisés (mêmes filtres appliqués de la même façon).
    """
    if utilisateur:
        requete = requete.filter(Conversation.utilisateur == utilisateur)
    if session_id:
        requete = requete.filter(Conversation.session_id == session_id)
    if recherche:
        requete = requete.filter(Conversation.question.ilike(f"%{recherche}%"))
    return requete


def sauvegarder_conversation(db, question, reponse, sources, session_id=None, utilisateur=None):
    """
    Enregistre un échange question/réponse dans la base.

    Paramètres
    ----------
    db : Session
        Session SQLAlchemy active.
    question : str
        Question de l'utilisateur.
    reponse : str
        Réponse générée.
    sources : list[str]
        Noms des fichiers sources utilisés.
    session_id : str | None
        Identifiant de session optionnel.
    utilisateur : str | None
        Propriétaire de la conversation.

    Retour
    ------
    Conversation
        L'objet enregistré avec son identifiant.
    """
    conversation = Conversation(
        question=question,
        reponse=reponse,
        sources=json.dumps(sources, ensure_ascii=False),
        session_id=session_id,
        utilisateur=utilisateur,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    logger.info("Conversation #%d sauvegardée.", conversation.id)
    return conversation


def lister_conversations(db, limite=50, decalage=0, session_id=None, utilisateur=None, recherche=None):
    """
    Récupère les conversations, des plus récentes aux plus anciennes.

    Paramètres
    ----------
    db : Session
        Session SQLAlchemy.
    limite : int
        Nombre maximum de résultats.
    decalage : int
        Décalage pour la pagination.
    session_id : str | None
        Filtre optionnel par session.
    utilisateur : str | None
        Filtre optionnel par propriétaire.
    recherche : str | None
        Filtre optionnel : ne garde que les questions contenant ce texte
        (recherche insensible à la casse).

    Retour
    ------
    list[Conversation]
        Liste des conversations correspondantes.
    """
    requete = db.query(Conversation).order_by(desc(Conversation.cree_le))
    requete = appliquer_filtres(requete, session_id, utilisateur, recherche)
    return requete.offset(decalage).limit(limite).all()


def compter_conversations(db, session_id=None, utilisateur=None, recherche=None):
    """Retourne le nombre de conversations correspondant aux filtres fournis."""
    requete = appliquer_filtres(db.query(Conversation), session_id, utilisateur, recherche)
    return requete.count()


def supprimer_conversation(db, conversation_id):
    """
    Supprime une conversation par son identifiant.

    Retour
    ------
    bool
        True si supprimée, False si introuvable.
    """
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        return False
    db.delete(conv)
    db.commit()
    return True


def vider_conversations(db, utilisateur=None):
    """
    Supprime des conversations en masse.

    Si utilisateur est fourni : ne supprime que les siennes.
    Si utilisateur vaut None : supprime TOUT l'historique.

    Retour
    ------
    int
        Nombre de conversations supprimées.
    """
    requete = appliquer_filtres(db.query(Conversation), utilisateur=utilisateur)
    total = requete.count()
    requete.delete(synchronize_session=False)
    db.commit()
    return total