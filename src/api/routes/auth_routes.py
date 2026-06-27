##############################################################
# Nom ......... : auth_routes.py
# Rôle ........ : Routes d'authentification de l'API RAG
#                 Enterprise. Création de comptes, connexion
#                 (jeton JWT), profil et liste des comptes
#                 (réservée aux admins).
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 26/06/2026
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
# Routeur dédié à l'authentification : toutes ses routes sont préfixées par
# "/auth" (ex. /auth/login, /auth/register).
router = APIRouter(prefix="/auth", tags=["Authentification"])


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
# Forme attendue du corps d'une demande d'inscription. Les contraintes Field
# sont validées AUTOMATIQUEMENT par FastAPI avant d'entrer dans la route : une
# requête non conforme est rejetée avec une erreur 422 sans écrire de code.
class RequeteInscription(BaseModel):
    # max_length=72 : limite imposée par bcrypt (mots de passe plus longs
    # non supportés). On le refuse proprement plutôt que de tronquer.
    nom_utilisateur: str = Field(..., min_length=3, max_length=50)
    mot_de_passe: str = Field(..., min_length=6, max_length=72)
    # pattern impose que le rôle soit exactement "user" ou "admin" : toute
    # autre valeur est refusée, ce qui évite de créer un rôle invalide.
    role: str = Field(default="user", pattern="^(user|admin)$")


# Réponse renvoyée après une connexion réussie : le jeton et quelques infos.
class ReponseJeton(BaseModel):
    access_token: str
    token_type: str = "bearer"  # convention OAuth2 ; le client renvoie « Bearer <jeton> »
    nom_utilisateur: str
    role: str


# Forme publique d'un utilisateur (ce qu'on expose). Le hachage du mot de passe
# n'y figure VOLONTAIREMENT pas : il ne doit jamais sortir de l'application.
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
    # exiger_admin : seul un administrateur déjà connecté peut créer des
    # comptes. Le « _ » signale qu'on ne se sert pas de la valeur retournée,
    # seul son contrôle d'accès compte (sinon la requête est rejetée avant).
    _=Depends(exiger_admin),
):
    """Crée un nouveau compte. Réservé aux administrateurs."""
    # La logique de création (hachage du mot de passe, vérification d'unicité)
    # est déléguée à creer_utilisateur (dans src.api.auth). « en_dict() »
    # renvoie la forme publique, et « ** » déballe ce dictionnaire en arguments.
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
    # Problème de « l'œuf et la poule » : créer un compte exige d'être admin,
    # mais au tout début il n'existe aucun admin. Cette route résout cela : elle
    # est ouverte (pas de exiger_admin), MAIS uniquement tant que la base est
    # vide. Dès qu'un utilisateur existe, elle se verrouille (403) et l'on doit
    # passer par /auth/register (réservé aux admins).
    if db.query(Utilisateur).count() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Des utilisateurs existent déjà. Utilisez /auth/register.",
        )
    # Le premier compte est forcément créé avec le rôle admin.
    nouveau = creer_utilisateur(db, requete.nom_utilisateur, requete.mot_de_passe, role="admin")
    return UtilisateurSortie(**nouveau.en_dict())


@router.post(
    "/login",
    response_model=ReponseJeton,
    summary="Se connecter et obtenir un jeton JWT",
)
def connexion(
    # OAuth2PasswordRequestForm : dépendance fournie par FastAPI qui lit les
    # champs « username » et « password » envoyés au format formulaire. Suivre
    # ce standard OAuth2 permet au bouton « Authorize » de la page /docs de
    # fonctionner directement, sans configuration supplémentaire.
    formulaire=Depends(OAuth2PasswordRequestForm),
    db=Depends(get_db),
):
    """
    Authentifie l'utilisateur et retourne un jeton JWT.
    Utilise le format OAuth2 standard pour rester compatible avec /docs.
    """
    # 1. Vérifier les identifiants (compare le mot de passe au hachage stocké).
    #    En cas d'échec, authentifier_utilisateur lève une erreur 401.
    utilisateur = authentifier_utilisateur(db, formulaire.username, formulaire.password)
    # 2. Forger un jeton JWT signé contenant le nom (« sub ») et le rôle. Ce
    #    jeton sera renvoyé par le client à chaque requête pour prouver son
    #    identité, sans avoir à renvoyer le mot de passe.
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
    # utilisateur_courant décode le jeton JWT de la requête et retrouve le
    # compte correspondant. Si le jeton est absent ou invalide, la requête est
    # rejetée avant d'arriver ici : pas besoin de vérification supplémentaire.
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
    # On convertit chaque utilisateur en sa forme publique (sans le hachage).
    return [UtilisateurSortie(**u.en_dict()) for u in utilisateurs]