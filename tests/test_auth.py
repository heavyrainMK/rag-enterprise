##############################################################
# Nom ......... : test_auth.py
# Rôle ........ : Tests du module d'authentification src/api/auth.py.
#                 Vérifie le hachage bcrypt, les jetons JWT, la
#                 gestion des utilisateurs et l'authentification.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.0.0 du 19/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_auth.py -v
# Dépendances . : pytest, sqlalchemy, fastapi, src.api.auth
##############################################################

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from src.core.database import Base, Utilisateur
from src.api.auth import (
    hacher_mot_de_passe,
    verifier_mot_de_passe,
    creer_jeton,
    decoder_jeton,
    creer_utilisateur,
    authentifier_utilisateur,
    trouver_utilisateur,
    secret_est_faible,
)


@pytest.fixture
def db_session():
    """Base SQLite en mémoire isolée pour chaque test."""
    moteur = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=moteur)
    Session = sessionmaker(bind=moteur)
    session = Session()
    yield session
    session.close()


class TestMotsDePasse:

    def test_hachage_different_du_clair(self):
        hache = hacher_mot_de_passe("monmotdepasse")
        assert hache != "monmotdepasse"
        assert len(hache) > 20

    def test_verifier_bon_mot_de_passe(self):
        hache = hacher_mot_de_passe("secret123")
        assert verifier_mot_de_passe("secret123", hache) is True

    def test_verifier_mauvais_mot_de_passe(self):
        hache = hacher_mot_de_passe("secret123")
        assert verifier_mot_de_passe("mauvais", hache) is False

    def test_deux_hachages_differents(self):
        h1 = hacher_mot_de_passe("meme_password")
        h2 = hacher_mot_de_passe("meme_password")
        assert h1 != h2
        assert verifier_mot_de_passe("meme_password", h1)
        assert verifier_mot_de_passe("meme_password", h2)


class TestJWT:

    def test_creer_et_decoder_jeton(self):
        jeton = creer_jeton({"sub": "alice", "role": "user"})
        contenu = decoder_jeton(jeton)
        assert contenu["sub"] == "alice"
        assert contenu["role"] == "user"

    def test_jeton_invalide_leve_401(self):
        with pytest.raises(HTTPException) as exc_info:
            decoder_jeton("jeton.invalide.ici")
        assert exc_info.value.status_code == 401

    def test_jeton_modifie_leve_401(self):
        jeton = creer_jeton({"sub": "alice"})
        modifie = jeton[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc_info:
            decoder_jeton(modifie)
        assert exc_info.value.status_code == 401


class TestGestionUtilisateurs:

    def test_creer_utilisateur(self, db_session):
        u = creer_utilisateur(db_session, "bob", "password123", role="user")
        assert u.id is not None
        assert str(u.nom_utilisateur) == "bob"
        assert str(u.role) == "user"
        assert str(u.mot_de_passe_hache) != "password123"

    def test_creer_admin(self, db_session):
        u = creer_utilisateur(db_session, "adminuser", "adminpass", role="admin")
        assert str(u.role) == "admin"

    def test_nom_en_double_leve_409(self, db_session):
        creer_utilisateur(db_session, "alice", "pass1")
        with pytest.raises(HTTPException) as exc_info:
            creer_utilisateur(db_session, "alice", "pass2")
        assert exc_info.value.status_code == 409

    def test_trouver_utilisateur(self, db_session):
        creer_utilisateur(db_session, "charlie", "pass")
        u = trouver_utilisateur(db_session, "charlie")
        assert u is not None
        assert str(u.nom_utilisateur) == "charlie"

    def test_utilisateur_inexistant_retourne_none(self, db_session):
        u = trouver_utilisateur(db_session, "fantome")
        assert u is None


class TestAuthentification:

    def test_identifiants_corrects(self, db_session):
        creer_utilisateur(db_session, "diana", "monpass")
        u = authentifier_utilisateur(db_session, "diana", "monpass")
        assert str(u.nom_utilisateur) == "diana"

    def test_mauvais_mot_de_passe(self, db_session):
        creer_utilisateur(db_session, "eve", "bonpass")
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "eve", "mauvaispass")
        assert exc_info.value.status_code == 401

    def test_utilisateur_inconnu(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "inconnu", "pass")
        assert exc_info.value.status_code == 401

    def test_compte_desactive(self, db_session):
        u = creer_utilisateur(db_session, "frank", "pass")
        setattr(u, "actif", 0)
        db_session.commit()
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "frank", "pass")
        assert exc_info.value.status_code == 403

    def test_en_dict_sans_mot_de_passe(self, db_session):
        u = creer_utilisateur(db_session, "grace", "secret")
        d = u.en_dict()
        assert "mot_de_passe_hache" not in d
        assert "mot_de_passe" not in d
        assert set(d.keys()) == {"id", "nom_utilisateur", "role", "actif", "cree_le"}


class TestSecret:

    def test_secret_par_defaut_est_faible(self):
        from src.api import auth
        assert auth.CLE_SECRETE == auth.SECRET_PAR_DEFAUT
        assert secret_est_faible() is True