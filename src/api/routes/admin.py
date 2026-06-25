##############################################################
# Nom ......... : admin.py
# Rôle ........ : Routes JSON du tableau de bord administrateur
#                 de l'API RAG Enterprise. Fournit les
#                 statistiques système, la liste des utilisateurs
#                 et l'activité récente. L'affichage est assuré
#                 par le frontend React (VueAdmin.tsx).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.0.0 du 23/06/2026
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
router = APIRouter(prefix="/admin", tags=["Administration"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
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
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Retourne les statistiques globales. Réservé aux admins."""
    from src.core.ingest import DOSSIER_PARTAGE, DOSSIER_USERS, EXTENSIONS_AUTORISEES

    maintenant = datetime.now(timezone.utc)
    debut_jour = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    debut_semaine = maintenant - timedelta(days=7)

    nb_utilisateurs = db.query(Utilisateur).count()
    nb_actifs = db.query(Utilisateur).filter(Utilisateur.actif == 1).count()
    nb_admins = db.query(Utilisateur).filter(Utilisateur.role == "admin").count()
    nb_conversations = db.query(Conversation).count()

    conv_jour = db.query(Conversation).filter(
        Conversation.cree_le >= debut_jour
    ).count()
    conv_semaine = db.query(Conversation).filter(
        Conversation.cree_le >= debut_semaine
    ).count()

    # Compter les documents : partagés (data/shared/) + privés (data/users/<nom>/)
    def compter(dossier):
        if not dossier.exists():
            return 0
        return sum(
            1 for f in dossier.rglob("*")
            if f.is_file() and f.suffix.lower() in EXTENSIONS_AUTORISEES
        )

    nb_documents = compter(DOSSIER_PARTAGE) + compter(DOSSIER_USERS)

    # Compter les morceaux dans ChromaDB
    nb_morceaux = 0
    try:
        from src.core.rag import charger_vectorstore
        vs = charger_vectorstore()
        nb_morceaux = vs._collection.count()
    except Exception:
        pass

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
    utilisateurs = db.query(Utilisateur).order_by(Utilisateur.cree_le).all()
    resultat = []
    for u in utilisateurs:
        nb = db.query(Conversation).filter(
            Conversation.utilisateur == u.nom_utilisateur
        ).count()
        resultat.append(StatsUtilisateur(
            id=u.id,
            nom_utilisateur=u.nom_utilisateur,
            role=u.role,
            actif=bool(u.actif),
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
    conversations = (
        db.query(Conversation)
        .order_by(Conversation.cree_le.desc())
        .limit(20)
        .all()
    )
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