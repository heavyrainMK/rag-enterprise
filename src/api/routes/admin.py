##############################################################
# Nom ......... : admin.py
# Rôle ........ : Routes JSON du tableau de bord administrateur
#                 de l'API RAG Enterprise. Fournit les
#                 statistiques système, la liste des utilisateurs
#                 et l'activité récente. L'affichage est assuré
#                 par le frontend React (VueAdmin.tsx).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.0.0 du 26/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, sqlalchemy, pydantic
##############################################################

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import exiger_admin
from src.core.database import get_db, Utilisateur, Conversation

logger = logging.getLogger(__name__)
# Un APIRouter regroupe plusieurs routes liées. Le préfixe "/admin" est ajouté
# automatiquement devant chaque route définie ici (ex. /stats devient
# /admin/stats), et le tag sert au classement dans la documentation auto.
router = APIRouter(prefix="/admin", tags=["Administration"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
# Ces classes décrivent la FORME exacte des réponses JSON renvoyées par les
# routes. FastAPI s'en sert pour deux choses : valider que les données
# envoyées correspondent bien au format annoncé, et documenter automatiquement
# l'API. Elles correspondent terme à terme aux interfaces TypeScript du
# frontend (VueAdmin.tsx), ce qui garantit que les deux côtés s'accordent.
class StatsSysteme(BaseModel):
    """Statistiques globales du système."""
    nb_utilisateurs: int
    nb_utilisateurs_actifs: int
    nb_admins: int
    nb_conversations: int
    conversations_aujourdhui: int
    conversations_semaine: int
    nb_documents: int
    nb_morceaux: int


class StatsUtilisateur(BaseModel):
    """Statistiques par utilisateur."""
    id: int
    nom_utilisateur: str
    role: str
    actif: bool
    cree_le: str
    nb_conversations: int


class ActiviteRecente(BaseModel):
    """Activité récente."""
    id: int
    nom_utilisateur: str
    question: str
    sources: list[str]
    cree_le: str


# ---------------------------------------------------------------------------
# Routes JSON
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=StatsSysteme, summary="Statistiques système")
def stats_systeme(
    # Injection de dépendances FastAPI : avant d'exécuter la fonction, FastAPI
    # appelle ces dépendances et passe leur résultat en argument.
    #   - get_db       : fournit une session de base de données ;
    #   - exiger_admin : vérifie que l'appelant est un administrateur (sinon la
    #     requête est rejetée AVANT d'entrer ici). Le « _ » indique qu'on ne se
    #     sert pas de sa valeur de retour : seul son effet de contrôle compte.
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Retourne les statistiques globales. Réservé aux admins."""
    from src.core.ingest import DOSSIER_PARTAGE, DOSSIER_USERS, EXTENSIONS_AUTORISEES

    # Bornes de temps pour les compteurs « aujourd'hui » et « cette semaine ».
    # On travaille en UTC pour éviter toute ambiguïté de fuseau horaire.
    maintenant = datetime.now(timezone.utc)
    debut_jour = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    debut_semaine = maintenant - timedelta(days=7)

    # Comptages via l'ORM SQLAlchemy. Chaque .query(...).count() se traduit en
    # une requête SQL « SELECT COUNT(*) ... » : on récupère un nombre, pas les
    # lignes elles-mêmes, ce qui est efficace.
    nb_utilisateurs = db.query(Utilisateur).count()
    nb_actifs = db.query(Utilisateur).filter(Utilisateur.actif == 1).count()
    nb_admins = db.query(Utilisateur).filter(Utilisateur.role == "admin").count()
    nb_conversations = db.query(Conversation).count()

    # Conversations créées depuis le début du jour, puis depuis 7 jours.
    conv_jour = db.query(Conversation).filter(
        Conversation.cree_le >= debut_jour
    ).count()
    conv_semaine = db.query(Conversation).filter(
        Conversation.cree_le >= debut_semaine
    ).count()

    # Compter les documents : partagés (data/shared/) + privés (data/users/<nom>/)
    # rglob("*") parcourt récursivement le dossier ; on ne compte que les vrais
    # fichiers dont l'extension est autorisée (on ignore dossiers et autres).
    def compter(dossier):
        if not dossier.exists():
            return 0
        return sum(
            1 for f in dossier.rglob("*")
            if f.is_file() and f.suffix.lower() in EXTENSIONS_AUTORISEES
        )

    nb_documents = compter(DOSSIER_PARTAGE) + compter(DOSSIER_USERS)

    # Compter les morceaux dans ChromaDB
    # Entouré d'un try/except volontairement permissif : si l'index n'existe
    # pas encore (aucune ingestion effectuée), on renvoie simplement 0 au lieu
    # de faire échouer toute la route des statistiques.
    nb_morceaux = 0
    try:
        from src.core.rag import charger_vectorstore
        vs = charger_vectorstore()
        nb_morceaux = vs._collection.count()
    except Exception:
        pass

    # Assemble toutes les valeurs dans le modèle de réponse. FastAPI le
    # convertira automatiquement en JSON conforme à StatsSysteme.
    return StatsSysteme(
        nb_utilisateurs=nb_utilisateurs,
        nb_utilisateurs_actifs=nb_actifs,
        nb_admins=nb_admins,
        nb_conversations=nb_conversations,
        conversations_aujourdhui=conv_jour,
        conversations_semaine=conv_semaine,
        nb_documents=nb_documents,
        nb_morceaux=nb_morceaux,
    )


@router.get("/users", response_model=list[StatsUtilisateur], summary="Stats par utilisateur")
def stats_utilisateurs(
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Retourne les stats de chaque utilisateur avec son nombre de conversations."""
    # On récupère tous les utilisateurs, triés par date de création.
    utilisateurs = db.query(Utilisateur).order_by(Utilisateur.cree_le).all()
    resultat = []
    for u in utilisateurs:
        # Pour chaque utilisateur, on compte ses conversations.
        # Remarque : c'est une requête par utilisateur (motif « N+1 »).
        # Acceptable ici vu le faible nombre d'utilisateurs ; sur une grande
        # base, on préférerait une seule requête groupée (GROUP BY).
        nb = db.query(Conversation).filter(
            Conversation.utilisateur == u.nom_utilisateur
        ).count()
        resultat.append(StatsUtilisateur(
            id=u.id,
            nom_utilisateur=u.nom_utilisateur,
            role=u.role,
            actif=bool(u.actif),
            # isoformat() convertit la date en texte standard (ex.
            # "2026-06-25T18:10:25"), directement exploitable par le frontend.
            cree_le=u.cree_le.isoformat(),
            nb_conversations=nb,
        ))
    return resultat


@router.get("/activity", response_model=list[ActiviteRecente], summary="Activité récente")
def activite_recente(
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Retourne les 20 dernières conversations, toutes sessions confondues."""
    # Tri par date décroissante (.desc()) + limite à 20 : on ne récupère que
    # les conversations les plus récentes, pas tout l'historique.
    conversations = (
        db.query(Conversation)
        .order_by(Conversation.cree_le.desc())
        .limit(20)
        .all()
    )
    # Transformation de chaque enregistrement en modèle de réponse :
    #   - « utilisateur or "anonyme" » : valeur de repli si le champ est vide ;
    #   - question[:100] : on tronque à 100 caractères pour un aperçu compact ;
    #   - sources_en_liste() : convertit les sources stockées en liste Python.
    return [
        ActiviteRecente(
            id=c.id,
            nom_utilisateur=c.utilisateur or "anonyme",
            question=c.question[:100],
            sources=c.sources_en_liste(),
            cree_le=c.cree_le.isoformat(),
        )
        for c in conversations
    ]