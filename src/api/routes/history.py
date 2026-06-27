##############################################################
# Nom ......... : history.py
# Rôle ........ : Historique des conversations de l'API RAG
#                 Enterprise. Consultation (paginée, par id),
#                 filtrage par session et suppression. L'historique
#                 est isolé par utilisateur ; un admin voit tout.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.3.0 du 27/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, pydantic, src.core.database, src.api.auth
##############################################################

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# On importe les dépendances d'authentification : utilisateur_courant
# identifie l'appelant à partir de son jeton JWT, exiger_admin refuse
# l'accès si l'appelant n'est pas administrateur. Ces deux fonctions sont
# injectées dans les routes via Depends(...) (voir plus bas).
from src.api.auth import utilisateur_courant, exiger_admin
# La couche d'accès aux données est entièrement déléguée à database :
# ce module n'écrit aucune requête SQL brute, il appelle des fonctions
# métier. Cela garde la logique HTTP (ici) séparée de la logique de
# persistance, et rend les routes lisibles et testables.
from src.core.database import (
    get_db,                    # fournit une session SQLAlchemy par requête
    lister_conversations,      # lecture paginée + filtres
    compter_conversations,     # total (pour la pagination côté client)
    supprimer_conversation,    # suppression unitaire
    vider_conversations,       # suppression en masse
    Conversation,              # le modèle ORM (table des conversations)
)

logger = logging.getLogger(__name__)
# prefix="/history" : toutes les routes de ce fichier sont préfixées,
# d'où des chemins comme GET /history/ ou DELETE /history/{id}. tags
# regroupe ces routes sous une même rubrique dans la doc Swagger générée.
router = APIRouter(prefix="/history", tags=["Historique"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
# Ces modèles décrivent la forme des données en sortie de l'API. Pydantic
# valide et sérialise automatiquement, et FastAPI s'en sert pour générer la
# documentation. On expose ainsi un contrat stable, découplé du modèle ORM
# interne : on choisit explicitement quels champs sortent vers le client.
class ConversationSortie(BaseModel):
    id: int
    question: str
    reponse: str
    sources: list[str]
    utilisateur: str | None
    session_id: str | None
    cree_le: str

    # from_attributes autorise Pydantic à lire les champs depuis un objet
    # quelconque (ici une instance ORM) et pas seulement depuis un dict.
    # C'est ce qui permettrait, au besoin, de construire le modèle
    # directement à partir d'un objet Conversation.
    model_config = {"from_attributes": True}


# Enveloppe de la liste paginée : on ne renvoie pas qu'un tableau, mais
# aussi total, limite et decalage. Le client a ainsi tout ce qu'il
# faut pour afficher une pagination (nombre de pages, page courante) sans
# avoir à le deviner.
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
    # Le str(...) est volontaire : selon le contexte, role peut être un
    # attribut de colonne SQLAlchemy plutôt qu'une chaîne nue. Le forcer en
    # str garantit une comparaison fiable quel que soit le type sous-jacent.
    return str(utilisateur.role) == "admin"


def filtre_proprietaire(utilisateur):
    """
    Détermine le filtre de propriété : un admin voit tout (None),
    un utilisateur standard ne voit que ses propres conversations.
    """
    # Renvoyer None signifie « aucun filtre de propriétaire » côté base :
    # l'admin récupère donc l'ensemble des conversations. Pour un compte
    # standard, on renvoie son nom, qui servira de clause WHERE. C'est ici
    # que se joue l'isolation par utilisateur, en un seul point centralisé.
    return None if est_admin(utilisateur) else str(utilisateur.nom_utilisateur)


def charger_conversation_autorisee(db, conversation_id, utilisateur):
    """
    Charge une conversation et vérifie que l'utilisateur y a accès.

    Renvoie 404 si elle n'existe pas OU si elle ne lui appartient pas.
    On utilise 404 (et non 403) pour ne pas révéler l'existence d'une
    conversation appartenant à quelqu'un d'autre.
    """
    # On factorise ici le « charger + contrôler l'accès » utilisé par
    # plusieurs routes (détail, suppression). Centraliser cette vérification
    # évite de la réécrire — et donc d'oublier un cas — à chaque endroit.
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    # Le piège de sécurité classique : distinguer « n'existe pas » de
    # « existe mais ne t'appartient pas » fuiterait de l'information (un
    # attaquant pourrait sonder les id existants). On répond donc 404 dans
    # les deux cas, avec le même message. Un admin, lui, passe toujours.
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
    # Query(...) déclare les paramètres d'URL avec leurs contraintes :
    # ge et le bornent les valeurs (1..100 résultats), ce qui protège la
    # base d'une demande déraisonnable et documente l'API automatiquement.
    limite: int = Query(default=20, ge=1, le=100, description="Nombre de résultats"),
    decalage: int = Query(default=0, ge=0, description="Décalage pour la pagination"),
    session_id: str | None = Query(default=None, description="Filtrer par session"),
    recherche: str | None = Query(default=None, description="Filtrer par mot-clé dans la question"),
    # Injection de dépendances FastAPI : la session DB et l'utilisateur
    # courant sont résolus automatiquement avant l'entrée dans la fonction.
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Retourne l'historique de l'utilisateur connecté, du plus récent au
    plus ancien. Un admin voit toutes les conversations.
    """
    # On calcule une seule fois le filtre de propriété, puis on l'applique
    # identiquement à la liste ET au comptage : indispensable pour que le
    # total corresponde bien à la liste filtrée (sinon la pagination ment).
    proprietaire = filtre_proprietaire(utilisateur)
    conversations = lister_conversations(
        db, limite=limite, decalage=decalage, session_id=session_id,
        utilisateur=proprietaire, recherche=recherche,
    )
    total = compter_conversations(
        db, session_id=session_id, utilisateur=proprietaire, recherche=recherche,
    )

    # en_dict() (méthode du modèle ORM) convertit chaque ligne en
    # dictionnaire ; on le déballe dans ConversationSortie, qui valide et
    # filtre les champs exposés. La donnée brute ne fuit jamais telle quelle.
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
    # Tout le contrôle d'accès est délégué à l'utilitaire : si l'appel
    # revient, c'est que l'accès est légitime. La route reste minimale.
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
    # On revérifie l'autorisation AVANT de supprimer (et non en se fiant à
    # un contrôle fait plus tôt) : une suppression est irréversible, la
    # vérification doit donc être au plus près de l'action destructrice.
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
    # Même filtre que pour la lecture : un standard ne vide que les siennes.
    # Réutiliser filtre_proprietaire garantit que « ce que je vide » est
    # exactement « ce que je vois », sans divergence de règle entre routes.
    proprietaire = filtre_proprietaire(utilisateur)
    total = vider_conversations(db, utilisateur=proprietaire)
    # On journalise les suppressions en masse : c'est une opération
    # sensible, une trace (qui ? combien ?) aide à l'audit et au diagnostic.
    logger.info("Historique vidé par '%s' : %d conversation(s).", utilisateur.nom_utilisateur, total)
    return {"message": f"{total} conversation(s) supprimée(s)."}


@router.delete(
    "/admin/all",
    summary="Vider TOUT l'historique (admin)",
)
def vider_tout_historique(
    db=Depends(get_db),
    # Cette route n'a pas besoin de connaître qui est l'admin, seulement
    # qu'il en est un. exiger_admin lève une erreur si ce n'est pas le
    # cas ; le _ indique qu'on ignore volontairement la valeur retournée.
    _=Depends(exiger_admin),
):
    """Supprime toutes les conversations de la base. Réservé aux admins."""
    # Sans filtre de propriétaire : on vide vraiment toute la table. D'où la
    # protection exiger_admin ci-dessus, garde-fou indispensable.
    total = vider_conversations(db)
    logger.info("Historique global vidé : %d conversation(s).", total)
    return {"message": f"{total} conversation(s) supprimée(s)."}