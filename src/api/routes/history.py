##############################################################
# Nom ......... : history.py
# Rôle ........ : Historique des conversations de l'API RAG
#                 Enterprise. Consultation (paginée, par id),
#                 filtrage par session et suppression. L'historique
#                 est isolé par utilisateur ; un admin voit tout.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.2.0 du 23/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, pydantic, src.core.database, src.api.auth
##############################################################

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.auth import utilisateur_courant, exiger_admin
from src.core.database import (
    get_db,
    lister_conversations,
    compter_conversations,
    supprimer_conversation,
    vider_conversations,
    Conversation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/history", tags=["Historique"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
class ConversationSortie(BaseModel):
    id: int
    question: str
    reponse: str
    sources: list[str]
    utilisateur: str | None
    session_id: str | None
    cree_le: str

    model_config = {"from_attributes": True}


class ReponseHistorique(BaseModel):
    conversations: list[ConversationSortie]
    total: int
    limite: int
    decalage: int


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------
def est_admin(utilisateur):
    """Retourne True si l'utilisateur est administrateur."""
    return str(utilisateur.role) == "admin"


def filtre_proprietaire(utilisateur):
    """
    Détermine le filtre de propriété : un admin voit tout (None),
    un utilisateur standard ne voit que ses propres conversations.
    """
    return None if est_admin(utilisateur) else str(utilisateur.nom_utilisateur)


def charger_conversation_autorisee(db, conversation_id, utilisateur):
    """
    Charge une conversation et vérifie que l'utilisateur y a accès.

    Renvoie 404 si elle n'existe pas OU si elle ne lui appartient pas.
    On utilise 404 (et non 403) pour ne pas révéler l'existence d'une
    conversation appartenant à quelqu'un d'autre.
    """
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv or (
        not est_admin(utilisateur)
        and str(conv.utilisateur) != str(utilisateur.nom_utilisateur)
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Conversation #{conversation_id} introuvable.",
        )
    return conv


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=ReponseHistorique,
    summary="Récupérer l'historique des conversations",
)
def consulter_historique(
    limite: int = Query(default=20, ge=1, le=100, description="Nombre de résultats"),
    decalage: int = Query(default=0, ge=0, description="Décalage pour la pagination"),
    session_id: str | None = Query(default=None, description="Filtrer par session"),
    recherche: str | None = Query(default=None, description="Filtrer par mot-clé dans la question"),
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Retourne l'historique de l'utilisateur connecté, du plus récent au
    plus ancien. Un admin voit toutes les conversations.
    """
    proprietaire = filtre_proprietaire(utilisateur)
    conversations = lister_conversations(
        db, limite=limite, decalage=decalage, session_id=session_id,
        utilisateur=proprietaire, recherche=recherche,
    )
    total = compter_conversations(
        db, session_id=session_id, utilisateur=proprietaire, recherche=recherche,
    )

    return ReponseHistorique(
        conversations=[ConversationSortie(**c.en_dict()) for c in conversations],
        total=total,
        limite=limite,
        decalage=decalage,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationSortie,
    summary="Détail d'une conversation",
)
def detail_conversation(
    conversation_id: int,
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Retourne une conversation par son id.
    Accessible seulement à son propriétaire (ou à un admin).
    """
    conv = charger_conversation_autorisee(db, conversation_id, utilisateur)
    return ConversationSortie(**conv.en_dict())


@router.delete(
    "/{conversation_id}",
    summary="Supprimer une conversation",
)
def retirer_conversation(
    conversation_id: int,
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Supprime une conversation.
    Un utilisateur ne peut supprimer que les siennes ; un admin, n'importe laquelle.
    """
    charger_conversation_autorisee(db, conversation_id, utilisateur)
    supprimer_conversation(db, conversation_id)
    return {"message": f"Conversation #{conversation_id} supprimée."}


@router.delete(
    "/",
    summary="Vider l'historique de l'utilisateur connecté",
)
def vider_historique(
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """Supprime les conversations de l'utilisateur connecté."""
    proprietaire = filtre_proprietaire(utilisateur)
    total = vider_conversations(db, utilisateur=proprietaire)
    logger.info("Historique vidé par '%s' : %d conversation(s).", utilisateur.nom_utilisateur, total)
    return {"message": f"{total} conversation(s) supprimée(s)."}


@router.delete(
    "/admin/all",
    summary="Vider TOUT l'historique (admin)",
)
def vider_tout_historique(
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Supprime toutes les conversations de la base. Réservé aux admins."""
    total = vider_conversations(db)
    logger.info("Historique global vidé : %d conversation(s).", total)
    return {"message": f"{total} conversation(s) supprimée(s)."}