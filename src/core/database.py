##############################################################
# Nom ......... : database.py
# Rôle ........ : Couche de persistance SQLite pour l'API RAG
#                 Enterprise. Définit les modèles SQLAlchemy
#                 (Conversation, Utilisateur), initialise la base
#                 et fournit les opérations de lecture/écriture
#                 pour l'historique et les comptes.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.2.0 du 27/06/2026
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
# Toute la persistance tient dans un seul fichier SQLite local : aucune base
# externe à installer ou administrer, ce qui colle à l'objectif « 100 % local »
# du projet. Le chemin est relatif au répertoire de lancement du serveur.
CHEMIN_BASE: Path = Path("rag_history.db")
URL_BASE: str = f"sqlite:///{CHEMIN_BASE}"

moteur = create_engine(
    URL_BASE,
    # check_same_thread=False : SQLite interdit par défaut qu'une connexion
    # soit utilisée par un autre thread que celui qui l'a créée. FastAPI
    # servant les requêtes sur plusieurs threads, on lève cette restriction.
    # C'est sûr ici car chaque requête obtient sa propre session (voir get_db).
    connect_args={"check_same_thread": False},
    # echo=False : on ne veut pas que SQLAlchemy logue toutes les requêtes SQL
    # (utile pour déboguer, trop verbeux en fonctionnement normal).
    echo=False,
)
# sessionmaker est une « usine » à sessions. autocommit/autoflush désactivés :
# on maîtrise explicitement quand on valide (commit) et quand on synchronise
# avec la base, ce qui rend le comportement transactionnel prévisible.
SessionLocale = sessionmaker(autocommit=False, autoflush=False, bind=moteur)


# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------
# Base est la classe racine de l'ORM : toutes les tables en héritent. SQLAlchemy
# s'en sert pour répertorier les modèles et générer les tables correspondantes.
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
    # question et reponse sont des Text (longueur non bornée) : on ne sait pas
    # à l'avance leur taille. À l'inverse, sources/utilisateur/session_id sont
    # des String bornés, suffisants pour des noms et identifiants courts.
    question = Column(Text, nullable=False)
    reponse = Column(Text, nullable=False)
    # Les sources sont stockées en JSON dans une colonne texte : SQLite ne gère
    # pas de type « liste ». On sérialise donc la liste en chaîne à l'écriture
    # et on la désérialise à la lecture (voir sources_en_liste plus bas).
    sources = Column(String(1000), default="[]")
    # index=True sur utilisateur et session_id : ce sont les colonnes sur
    # lesquelles on filtre le plus souvent (historique par utilisateur, par
    # session). L'index accélère ces recherches au prix d'un léger surcoût en
    # écriture, compromis gagnant ici.
    utilisateur = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    # default = une fonction (lambda) et non une valeur fixe : on veut que
    # l'horodatage soit calculé au MOMENT de l'insertion de chaque ligne. Un
    # datetime.now() écrit directement serait figé à l'import du module.
    cree_le = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def sources_en_liste(self):
        """Transforme le champ sources (JSON) en liste Python."""
        # Lecture défensive : si le champ est absent ou contient un JSON
        # corrompu, on retombe sur une liste vide plutôt que de laisser une
        # exception remonter jusqu'à l'API. Une donnée d'historique abîmée ne
        # doit pas casser l'affichage de tout l'historique.
        try:
            brut = str(self.sources) if self.sources is not None else "[]"
            return json.loads(brut)
        except json.JSONDecodeError:
            return []

    def en_dict(self):
        """Transforme l'objet en dictionnaire pour l'API."""
        # Conversion explicite vers un dictionnaire prêt pour l'API : on
        # désérialise les sources et on formate la date en ISO 8601, format
        # standard et non ambigu que le frontend sait parser directement.
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
    # unique=True garantit au niveau base qu'on ne peut pas créer deux comptes
    # de même nom : une double protection qui complète la vérification applicative
    # de creer_utilisateur (la base refuserait l'insertion en cas de course).
    nom_utilisateur = Column(String(100), unique=True, nullable=False, index=True)
    # On stocke uniquement le hachage bcrypt, jamais le mot de passe en clair.
    # La taille 200 laisse de la marge au-delà des ~60 caractères d'un hachage.
    mot_de_passe_hache = Column(String(200), nullable=False)
    role = Column(String(20), default="user", nullable=False)
    # actif est un entier (0/1) car SQLite n'a pas de type booléen natif. On
    # reconvertit en vrai booléen Python à la sortie (en_dict ci-dessous, et
    # bool(...) côté auth), pour exposer une valeur propre au reste du code.
    actif = Column(Integer, default=1)
    cree_le = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def en_dict(self):
        """Transforme l'objet en dictionnaire pour l'API."""
        # On ne renvoie JAMAIS mot_de_passe_hache : il n'a aucune raison de
        # quitter le serveur. Le sérialiseur ne liste que les champs publics,
        # et reconvertit actif en booléen pour le frontend.
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
    # create_all est idempotent : il ne crée que les tables absentes, donc on
    # peut l'appeler à chaque démarrage sans risque. On enchaîne ensuite la
    # migration ponctuelle pour les bases antérieures à l'isolation utilisateur.
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
    # SQLAlchemy ne gère pas l'évolution de schéma sur une table existante :
    # create_all n'ajoute pas une colonne à une table déjà créée. On fait donc
    # cette micro-migration à la main, de façon défensive. L'import est local
    # car inspect/text ne servent qu'ici.
    from sqlalchemy import inspect, text

    inspecteur = inspect(moteur)
    # Si la table n'existe pas encore, rien à migrer : create_all l'aura créée
    # avec la bonne structure. On sort tôt.
    if "conversations" not in inspecteur.get_table_names():
        return
    # On n'ajoute la colonne que si elle est réellement absente : c'est ce test
    # qui rend la migration idempotente (relançable sans erreur « colonne déjà
    # existante »).
    colonnes = {col["name"] for col in inspecteur.get_columns("conversations")}
    if "utilisateur" in colonnes:
        return
    # moteur.begin() ouvre une transaction qui se valide automatiquement à la
    # sortie du bloc (ou s'annule en cas d'erreur) : l'ALTER TABLE est donc
    # atomique. text(...) exécute du SQL brut, ici nécessaire car c'est une
    # opération de schéma que l'ORM ne couvre pas.
    with moteur.begin() as connexion:
        connexion.execute(text("ALTER TABLE conversations ADD COLUMN utilisateur VARCHAR(100)"))
    logger.info("Migration : colonne 'utilisateur' ajoutée à 'conversations'.")


def get_db():
    """
    Fournit une session de base de données pour FastAPI.
    La session est fermée automatiquement après usage.
    """
    # Patron classique d'injection de dépendance FastAPI : on crée une session
    # par requête, on la « yield » à la route, et le finally garantit sa
    # fermeture même si la route lève une exception. Une session par requête
    # évite tout partage d'état entre requêtes concurrentes.
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
    # Point unique de définition des filtres : lister, compter et vider passent
    # tous par ici. C'est ce qui garantit que « ce que je liste », « ce que je
    # compte » et « ce que je vide » s'appuient EXACTEMENT sur les mêmes
    # critères. Toute divergence (ex. un total qui ne correspond pas à la liste)
    # serait sinon un bug difficile à traquer.
    if utilisateur:
        requete = requete.filter(Conversation.utilisateur == utilisateur)
    if session_id:
        requete = requete.filter(Conversation.session_id == session_id)
    if recherche:
        # ilike = LIKE insensible à la casse ; les « % » entourant le terme en
        # font une recherche « contient ». La requête est paramétrée par
        # SQLAlchemy, ce qui évite toute injection SQL via le mot-clé saisi.
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
        # On sérialise la liste de sources en JSON pour la stocker dans la
        # colonne texte. ensure_ascii=False préserve les accents lisibles dans
        # la base plutôt que de les échapper en séquences \uXXXX.
        sources=json.dumps(sources, ensure_ascii=False),
        session_id=session_id,
        utilisateur=utilisateur,
    )
    db.add(conversation)
    db.commit()
    # refresh recharge l'objet après commit pour récupérer l'id auto-généré par
    # la base, qu'on journalise et qu'on renvoie à l'appelant.
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
    # order_by(desc(cree_le)) : du plus récent au plus ancien, l'ordre attendu
    # pour un historique. On applique ensuite les filtres communs, puis offset
    # et limit réalisent la pagination (sauter N résultats, en prendre au plus M).
    requete = db.query(Conversation).order_by(desc(Conversation.cree_le))
    requete = appliquer_filtres(requete, session_id, utilisateur, recherche)
    return requete.offset(decalage).limit(limite).all()


def compter_conversations(db, session_id=None, utilisateur=None, recherche=None):
    """Retourne le nombre de conversations correspondant aux filtres fournis."""
    # On compte avec les MÊMES filtres que lister_conversations (mais sans
    # pagination) : c'est ce total qui permet au client de calculer le nombre
    # de pages. Réutiliser appliquer_filtres garantit la cohérence des deux.
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
    # On renvoie un booléen plutôt que de lever une erreur si l'objet n'existe
    # pas : c'est l'appelant (la route) qui décide quoi faire d'une absence. Ici
    # le contrôle d'accès a déjà eu lieu en amont (voir history.py), cette
    # fonction se concentre sur l'opération de suppression elle-même.
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
    # Le même paramètre utilisateur commande deux comportements bien distincts :
    # filtré (un compte vide les siennes) ou non filtré (un admin vide tout).
    # C'est exactement la sémantique attendue par les routes de history.py.
    requete = appliquer_filtres(db.query(Conversation), utilisateur=utilisateur)
    # On compte AVANT de supprimer pour pouvoir renvoyer le nombre d'éléments
    # effacés (information utile à journaliser et à retourner au client).
    total = requete.count()
    # synchronize_session=False : suppression en masse directement en base, sans
    # synchroniser les objets déjà chargés en mémoire dans la session. C'est le
    # mode le plus efficace ici, car on ne réutilise pas ces objets ensuite.
    requete.delete(synchronize_session=False)
    db.commit()
    return total