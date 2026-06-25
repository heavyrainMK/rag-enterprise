##############################################################
# Nom ......... : auth_routes.py
# Rôle ........ : Routes d'authentification de l'API RAG
#                 Enterprise. Création de comptes, connexion
#                 (jeton JWT), profil et liste des comptes
#                 (réservée aux admins).
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 23/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, pydantic, sqlalchemy, src.api.auth
##############################################################

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from src.core.database import get_db, Utilisateur
from src.api.auth import (
    authentifier_utilisateur,
    creer_jeton,
    creer_utilisateur,
    utilisateur_courant,
    exiger_admin,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentification"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
class RequeteInscription(BaseModel):
    # max_length=72 : limite imposée par bcrypt (mots de passe plus longs
    # non supportés). On le refuse proprement plutôt que de tronquer.
    nom_utilisateur: str = Field(..., min_length=3, max_length=50)
    mot_de_passe: str = Field(..., min_length=6, max_length=72)
    role: str = Field(default="user", pattern="^(user|admin)$")


class ReponseJeton(BaseModel):
    access_token: str
    token_type: str = "bearer"
    nom_utilisateur: str
    role: str


class UtilisateurSortie(BaseModel):
    id: int
    nom_utilisateur: str
    role: str
    actif: bool
    cree_le: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    response_model=UtilisateurSortie,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un compte utilisateur",
)
def inscription(
    requete: RequeteInscription,
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Crée un nouveau compte. Réservé aux administrateurs."""
    nouveau = creer_utilisateur(db, requete.nom_utilisateur, requete.mot_de_passe, requete.role)
    return UtilisateurSortie(**nouveau.en_dict())


@router.post(
    "/register/first-admin",
    response_model=UtilisateurSortie,
    status_code=status.HTTP_201_CREATED,
    summary="Créer le premier compte administrateur",
)
def inscription_premier_admin(
    requete: RequeteInscription,
    db=Depends(get_db),
):
    """
    Crée le tout premier compte admin.
    Ne fonctionne que si aucun utilisateur n'existe encore.
    """
    if db.query(Utilisateur).count() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Des utilisateurs existent déjà. Utilisez /auth/register.",
        )
    nouveau = creer_utilisateur(db, requete.nom_utilisateur, requete.mot_de_passe, role="admin")
    return UtilisateurSortie(**nouveau.en_dict())


@router.post(
    "/login",
    response_model=ReponseJeton,
    summary="Se connecter et obtenir un jeton JWT",
)
def connexion(
    formulaire=Depends(OAuth2PasswordRequestForm),
    db=Depends(get_db),
):
    """
    Authentifie l'utilisateur et retourne un jeton JWT.
    Utilise le format OAuth2 standard pour rester compatible avec /docs.
    """
    utilisateur = authentifier_utilisateur(db, formulaire.username, formulaire.password)
    jeton = creer_jeton({"sub": utilisateur.nom_utilisateur, "role": utilisateur.role})
    logger.info("Connexion réussie : %s", utilisateur.nom_utilisateur)
    return ReponseJeton(
        access_token=jeton,
        token_type="bearer",
        nom_utilisateur=utilisateur.nom_utilisateur,
        role=utilisateur.role,
    )


@router.get(
    "/me",
    response_model=UtilisateurSortie,
    summary="Profil de l'utilisateur connecté",
)
def mon_profil(utilisateur=Depends(utilisateur_courant)):
    """Retourne les informations du compte connecté."""
    return UtilisateurSortie(**utilisateur.en_dict())


@router.get(
    "/users",
    response_model=list[UtilisateurSortie],
    summary="Liste des utilisateurs (admin)",
)
def lister_utilisateurs(
    db=Depends(get_db),
    _=Depends(exiger_admin),
):
    """Retourne la liste de tous les utilisateurs. Réservé aux admins."""
    utilisateurs = db.query(Utilisateur).order_by(Utilisateur.cree_le).all()
    return [UtilisateurSortie(**u.en_dict()) for u in utilisateurs]