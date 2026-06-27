##############################################################
# Nom ......... : stream.py
# Rôle ........ : Route de streaming SSE (Server-Sent Events)
#                 de l'API RAG Enterprise. Génère la réponse
#                 token par token comme ChatGPT, puis sauvegarde
#                 l'échange dans l'historique à la fin.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.2.0 du 27/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, src.core.rag, src.core.database, src.api.auth
##############################################################

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.auth import utilisateur_courant
from src.api.schemas import RequeteChat
from src.core.database import get_db, sauvegarder_conversation
# repondre_stream est le cœur RAG : un générateur qui produit des
# événements au fil de l'eau (tokens, sources, fin, erreur). REPONSE_SANS_
# CONTEXTE est le texte de repli quand aucune source pertinente n'est
# trouvée : on l'importe pour pouvoir l'archiver tel quel dans l'historique.
from src.core.rag import repondre_stream, REPONSE_SANS_CONTEXTE

logger = logging.getLogger(__name__)
# Pas de prefix ici (contrairement à history.py) : la route /chat/stream
# vit à la racine de l'API. Le tag ne sert qu'au regroupement Swagger.
router = APIRouter(tags=["RAG Streaming"])


def _evenement(donnees):
    """Met en forme un dictionnaire en ligne SSE valide."""
    # Le protocole SSE impose un format texte précis : chaque message est une
    # ligne « data: <charge utile> » suivie d'une LIGNE VIDE (le double \n\n
    # marque la fin de l'événement). Omettre ce double saut casserait le
    # découpage côté client. ensure_ascii=False préserve les accents (é, à)
    # au lieu de les échapper en \uXXXX, ce qui évite tout souci d'affichage.
    return f"data: {json.dumps(donnees, ensure_ascii=False)}\n\n"


@router.post(
    "/chat/stream",
    summary="Streaming SSE - réponse token par token",
    # On annonce explicitement StreamingResponse : FastAPI sait ainsi qu'il
    # ne s'agit pas d'une réponse JSON classique mais d'un flux continu.
    response_class=StreamingResponse,
)
async def chat_stream(
    requete: RequeteChat,
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Génère la réponse RAG en streaming via Server-Sent Events.
    Le client reçoit les tokens au fur et à mesure. La réponse complète
    est sauvegardée dans l'historique une fois le streaming terminé.
    """
    # On journalise dès l'entrée, mais on tronque la question à 60 caractères :
    # assez pour le diagnostic, sans gonfler les logs ni y déverser un texte
    # potentiellement long ou sensible saisi par l'utilisateur.
    logger.info(
        "POST /chat/stream | utilisateur='%s' | question='%s'",
        utilisateur.nom_utilisateur,
        requete.question[:60],
    )

    def generer():
        """Générateur SSE - parcourt les tokens du système RAG."""
        # Ce générateur est le « tuyau » : StreamingResponse le consomme et
        # envoie chaque yield au client immédiatement. On accumule au passage
        # les tokens et les sources, car on en aura besoin À LA FIN pour
        # reconstituer la réponse complète et l'archiver (le client, lui, a
        # déjà tout reçu morceau par morceau).
        tokens = []
        sources = []

        try:
            # On relaie les événements du moteur RAG en les retraduisant en
            # lignes SSE. On ne fait pas confiance aveuglément : on filtre par
            # type connu, ce qui évite d'émettre n'importe quoi vers le client.
            for evenement in repondre_stream(
                requete.question,
                # On force le nom en str : l'isolation par utilisateur côté RAG
                # repose dessus, et on veut une valeur scalaire, pas un attribut
                # ORM (même précaution que dans history.py).
                nom_utilisateur=str(utilisateur.nom_utilisateur),
            ):
                type_evenement = evenement.get("type")

                if type_evenement == "token":
                    # Cas nominal : un fragment de texte généré. On le mémorise
                    # (pour la sauvegarde finale) ET on le pousse au client.
                    token = evenement["content"]
                    tokens.append(token)
                    yield _evenement({"type": "token", "content": token})

                elif type_evenement == "no_context":
                    # Garde-fou anti-hallucination : aucune source pertinente,
                    # le LLM n'est pas appelé. On transmet un événement dédié
                    # pour que l'interface affiche un message honnête plutôt
                    # qu'une réponse inventée.
                    yield _evenement({"type": "no_context", "content": evenement["content"]})

                elif type_evenement == "done":
                    # Signal de fin propre : le moteur livre la liste finale des
                    # sources (et leur ventilation privée/partagée). On la
                    # capture dans sources pour la persistance, et on la
                    # renvoie au client pour qu'il affiche les références.
                    sources = evenement.get("sources", [])
                    yield _evenement({
                        "type": "done",
                        "sources": sources,
                        "private_sources": evenement.get("private_sources", []),
                        "shared_sources": evenement.get("shared_sources", []),
                    })

                elif type_evenement == "error":
                    # Erreur signalée par le moteur RAG lui-même. On la propage
                    # puis on return : inutile (et risqué) de poursuivre le
                    # flux ou de sauvegarder une réponse partielle erronée.
                    yield _evenement({"type": "error", "content": evenement["content"]})
                    return

        except Exception as exc:
            # Filet de sécurité pour toute erreur NON anticipée par le moteur
            # (panne réseau, exception inattendue...). On la trace côté serveur
            # avec la pile complète (exc_info=True) pour le diagnostic, mais on
            # n'envoie au client qu'un événement d'erreur, sans casser la
            # connexion brutalement. Puis on return : on n'archive rien.
            logger.error("Erreur SSE inattendue : %s", exc, exc_info=True)
            yield _evenement({"type": "error", "content": str(exc)})
            return

        # --- À partir d'ici, le streaming s'est terminé sans erreur ---
        # Sauvegarde dans l'historique après le streaming complet
        # On reconstitue la réponse en recollant les tokens. Si la liste est
        # vide (cas « no_context »), on archive le texte de repli standard,
        # pour que l'historique reflète fidèlement ce qu'a vu l'utilisateur.
        reponse_finale = "".join(tokens) if tokens else REPONSE_SANS_CONTEXTE
        try:
            sauvegarder_conversation(
                db=db,
                question=requete.question,
                reponse=reponse_finale,
                sources=sources,
                # session_effective choisit l'identifiant de session à
                # utiliser (celui fourni par le client, ou un défaut lié à
                # l'utilisateur) : la logique est centralisée dans le schéma.
                session_id=requete.session_effective(utilisateur.nom_utilisateur),
                utilisateur=str(utilisateur.nom_utilisateur),
            )
        except Exception as exc:
            # Choix de conception assumé : si l'archivage échoue, on ne casse
            # PAS l'expérience — l'utilisateur a déjà reçu sa réponse. On se
            # contente d'un warning. L'historique est secondaire face au service
            # rendu ; on dégrade proprement plutôt que de remonter une erreur.
            logger.warning("Impossible de sauvegarder l'historique : %s", exc)

    return StreamingResponse(
        generer(),
        # Type MIME normatif du SSE : c'est lui qui dit au navigateur « ceci
        # est un flux d'événements », pas une page ni un JSON.
        media_type="text/event-stream",
        headers={
            # Empêche tout cache de figer la réponse : un flux temps réel ne
            # doit jamais être servi depuis un cache.
            "Cache-Control": "no-cache",
            # En-tête clé : désactive la mise en tampon de nginx. Sans lui, le
            # proxy accumulerait les tokens et les délivrerait d'un bloc, ce qui
            # anéantirait l'effet « token par token ». C'est LE réglage qui fait
            # que le streaming fonctionne réellement derrière un reverse proxy.
            "X-Accel-Buffering": "no",
            # Maintient la connexion ouverte pendant toute la durée du flux.
            "Connection": "keep-alive",
        },
    )