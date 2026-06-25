##############################################################
# Nom ......... : stream.py
# Rôle ........ : Route de streaming SSE (Server-Sent Events)
#                 de l'API RAG Enterprise. Génère la réponse
#                 token par token comme ChatGPT, puis sauvegarde
#                 l'échange dans l'historique à la fin.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.2.0 du 24/06/2026
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
from src.core.rag import repondre_stream, REPONSE_SANS_CONTEXTE

logger = logging.getLogger(__name__)
router = APIRouter(tags=["RAG Streaming"])


def _evenement(donnees):
    """Met en forme un dictionnaire en ligne SSE valide."""
    return f"data: {json.dumps(donnees, ensure_ascii=False)}\n\n"


@router.post(
    "/chat/stream",
    summary="Streaming SSE - réponse token par token",
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
    logger.info(
        "POST /chat/stream | utilisateur='%s' | question='%s'",
        utilisateur.nom_utilisateur,
        requete.question[:60],
    )

    def generer():
        """Générateur SSE - parcourt les tokens du système RAG."""
        tokens = []
        sources = []

        try:
            for evenement in repondre_stream(
                requete.question,
                nom_utilisateur=str(utilisateur.nom_utilisateur),
            ):
                type_evenement = evenement.get("type")

                if type_evenement == "token":
                    token = evenement["content"]
                    tokens.append(token)
                    yield _evenement({"type": "token", "content": token})

                elif type_evenement == "no_context":
                    yield _evenement({"type": "no_context", "content": evenement["content"]})

                elif type_evenement == "done":
                    sources = evenement.get("sources", [])
                    yield _evenement({
                        "type": "done",
                        "sources": sources,
                        "private_sources": evenement.get("private_sources", []),
                        "shared_sources": evenement.get("shared_sources", []),
                    })

                elif type_evenement == "error":
                    yield _evenement({"type": "error", "content": evenement["content"]})
                    return

        except Exception as exc:
            logger.error("Erreur SSE inattendue : %s", exc, exc_info=True)
            yield _evenement({"type": "error", "content": str(exc)})
            return

        # Sauvegarde dans l'historique après le streaming complet
        reponse_finale = "".join(tokens) if tokens else REPONSE_SANS_CONTEXTE
        try:
            sauvegarder_conversation(
                db=db,
                question=requete.question,
                reponse=reponse_finale,
                sources=sources,
                session_id=requete.session_effective(utilisateur.nom_utilisateur),
                utilisateur=str(utilisateur.nom_utilisateur),
            )
        except Exception as exc:
            logger.warning("Impossible de sauvegarder l'historique : %s", exc)

    return StreamingResponse(
        generer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )