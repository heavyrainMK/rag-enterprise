##############################################################
# Nom ......... : test_auth.py
# Rôle ........ : Tests du module d'authentification src/api/auth.py.
#                 Vérifie le hachage bcrypt, les jetons JWT, la
#                 gestion des utilisateurs et l'authentification.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 27/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_auth.py -v
# Dépendances . : pytest, sqlalchemy, fastapi, src.api.auth
##############################################################

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from src.core.database import Base
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
    # Une base « :memory: » vit uniquement en RAM : rapide et jetable. Comme la
    # fixture est recréée à chaque test, chacun part d'une base vierge, sans
    # dépendre de l'ordre d'exécution ni laisser de trace pour le suivant. C'est
    # le principe d'isolation des tests : aucun effet de bord partagé.
    moteur = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # On reconstruit le schéma à partir des mêmes modèles que la vraie appli :
    # on teste donc bien la structure réelle, pas une maquette.
    Base.metadata.create_all(bind=moteur)
    Session = sessionmaker(bind=moteur)
    session = Session()
    # yield rend la session au test ; le code après le yield s'exécute APRÈS le
    # test (nettoyage), même s'il a échoué. On ferme proprement la session.
    yield session
    session.close()


class TestMotsDePasse:

    def test_hachage_different_du_clair(self):
        # Propriété la plus fondamentale : le hachage ne doit JAMAIS ressembler
        # au mot de passe en clair. On vérifie aussi une longueur plausible de
        # hachage bcrypt, pour écarter le cas où la fonction renverrait l'entrée
        # telle quelle ou une chaîne vide.
        hache = hacher_mot_de_passe("monmotdepasse")
        assert hache != "monmotdepasse"
        assert len(hache) > 20

    def test_verifier_bon_mot_de_passe(self):
        # Le bon mot de passe doit être reconnu : sans cela, personne ne pourrait
        # se connecter. C'est le chemin nominal du couple hacher/vérifier.
        hache = hacher_mot_de_passe("secret123")
        assert verifier_mot_de_passe("secret123", hache) is True

    def test_verifier_mauvais_mot_de_passe(self):
        # Le pendant indispensable : un mauvais mot de passe doit être rejeté.
        # Les deux tests ensemble prouvent que la vérification discrimine
        # réellement, et ne renvoie pas toujours la même réponse.
        hache = hacher_mot_de_passe("secret123")
        assert verifier_mot_de_passe("mauvais", hache) is False

    def test_deux_hachages_differents(self):
        # On vérifie l'effet du sel aléatoire de bcrypt : hacher DEUX FOIS le
        # même mot de passe donne deux empreintes différentes. C'est ce qui
        # empêche un attaquant de repérer que deux comptes ont le même mot de
        # passe. Et malgré ces empreintes distinctes, la vérification réussit
        # pour les deux : le sel est bien intégré au hachage, pas perdu.
        h1 = hacher_mot_de_passe("meme_password")
        h2 = hacher_mot_de_passe("meme_password")
        assert h1 != h2
        assert verifier_mot_de_passe("meme_password", h1)
        assert verifier_mot_de_passe("meme_password", h2)


class TestJWT:

    def test_creer_et_decoder_jeton(self):
        # Aller-retour de base : ce qu'on encode dans le jeton doit se retrouver
        # à l'identique après décodage. On valide ainsi que la charge utile
        # (sujet, rôle) survit au cycle signature/vérification.
        jeton = creer_jeton({"sub": "alice", "role": "user"})
        contenu = decoder_jeton(jeton)
        assert contenu["sub"] == "alice"
        assert contenu["role"] == "user"

    def test_jeton_invalide_leve_401(self):
        # Une chaîne qui n'est pas un vrai jeton doit être refusée par un 401, et
        # non provoquer une erreur 500 non gérée. On vérifie donc que l'erreur
        # est bien traduite en réponse HTTP propre (cf. decoder_jeton).
        with pytest.raises(HTTPException) as exc_info:
            decoder_jeton("jeton.invalide.ici")
        assert exc_info.value.status_code == 401

    def test_jeton_modifie_leve_401(self):
        # Test anti-falsification : on prend un jeton VALIDE et on altère sa fin
        # (la signature). Le décodage doit alors échouer en 401. C'est la preuve
        # que la signature protège réellement le contenu : on ne peut pas
        # bricoler un jeton sans connaître le secret.
        jeton = creer_jeton({"sub": "alice"})
        modifie = jeton[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc_info:
            decoder_jeton(modifie)
        assert exc_info.value.status_code == 401


class TestGestionUtilisateurs:

    def test_creer_utilisateur(self, db_session):
        # Création nominale : l'utilisateur reçoit un id (généré par la base), ses
        # champs sont corrects, et surtout son mot de passe est stocké HACHÉ, pas
        # en clair. Ce dernier assert reverifie la règle d'or au niveau création.
        u = creer_utilisateur(db_session, "bob", "password123", role="user")
        assert u.id is not None
        assert str(u.nom_utilisateur) == "bob"
        assert str(u.role) == "user"
        assert str(u.mot_de_passe_hache) != "password123"

    def test_creer_admin(self, db_session):
        # On peut créer un compte avec le rôle admin : le rôle demandé est bien
        # persisté. C'est ce rôle que exiger_admin contrôlera ensuite.
        u = creer_utilisateur(db_session, "adminuser", "adminpass", role="admin")
        assert str(u.role) == "admin"

    def test_nom_en_double_leve_409(self, db_session):
        # L'unicité du nom est garantie : créer deux comptes du même nom lève un
        # 409 (conflit). On valide ainsi la vérification applicative en amont,
        # qui double la contrainte unique de la base.
        creer_utilisateur(db_session, "alice", "pass1")
        with pytest.raises(HTTPException) as exc_info:
            creer_utilisateur(db_session, "alice", "pass2")
        assert exc_info.value.status_code == 409

    def test_trouver_utilisateur(self, db_session):
        # Un utilisateur créé doit être retrouvable par son nom : c'est la brique
        # de lecture sur laquelle s'appuient l'authentification et les routes.
        creer_utilisateur(db_session, "charlie", "pass")
        u = trouver_utilisateur(db_session, "charlie")
        assert u is not None
        assert str(u.nom_utilisateur) == "charlie"

    def test_utilisateur_inexistant_retourne_none(self, db_session):
        # Cas négatif explicite : chercher un nom absent renvoie None (et ne lève
        # pas d'erreur). Les appelants s'appuient sur ce None pour décider de la
        # suite, donc ce contrat doit être testé.
        u = trouver_utilisateur(db_session, "fantome")
        assert u is None


class TestAuthentification:

    def test_identifiants_corrects(self, db_session):
        # Chemin nominal complet : un compte existant avec le bon mot de passe
        # s'authentifie et renvoie l'utilisateur attendu.
        creer_utilisateur(db_session, "diana", "monpass")
        u = authentifier_utilisateur(db_session, "diana", "monpass")
        assert str(u.nom_utilisateur) == "diana"

    def test_mauvais_mot_de_passe(self, db_session):
        # Bon nom mais mauvais mot de passe : 401. À rapprocher du test suivant.
        creer_utilisateur(db_session, "eve", "bonpass")
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "eve", "mauvaispass")
        assert exc_info.value.status_code == 401

    def test_utilisateur_inconnu(self, db_session):
        # Nom inexistant : 401 AUSSI, exactement le même code que le mauvais mot
        # de passe ci-dessus. Ces deux tests pris ensemble verrouillent une
        # propriété de sécurité : on ne distingue pas « nom inconnu » de « mot de
        # passe faux », pour ne pas permettre d'énumérer les comptes existants.
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "inconnu", "pass")
        assert exc_info.value.status_code == 401

    def test_compte_desactive(self, db_session):
        # Identifiants corrects MAIS compte désactivé : 403 (et non 401). Le code
        # diffère volontairement, car ici l'identité est prouvée ; ce qui manque,
        # c'est le droit d'accès. On force actif=0 puis on vérifie le refus.
        u = creer_utilisateur(db_session, "frank", "pass")
        setattr(u, "actif", 0)
        db_session.commit()
        with pytest.raises(HTTPException) as exc_info:
            authentifier_utilisateur(db_session, "frank", "pass")
        assert exc_info.value.status_code == 403

    def test_en_dict_sans_mot_de_passe(self, db_session):
        # Test de non-fuite : la sérialisation d'un utilisateur ne doit JAMAIS
        # contenir le hachage du mot de passe. On vérifie l'absence des deux
        # clés sensibles ET, plus strict, que l'ensemble EXACT des clés exposées
        # est celui attendu. Ainsi, si quelqu'un ajoute un champ sensible plus
        # tard, ce test le détecte immédiatement.
        u = creer_utilisateur(db_session, "grace", "secret")
        d = u.en_dict()
        assert "mot_de_passe_hache" not in d
        assert "mot_de_passe" not in d
        assert set(d.keys()) == {"id", "nom_utilisateur", "role", "actif", "cree_le"}


class TestSecret:

    def test_secret_par_defaut_est_faible(self):
        # En environnement de test, aucune variable JWT_SECRET_KEY n'est définie :
        # la clé retombe donc sur sa valeur par défaut, que secret_est_faible doit
        # signaler comme faible. C'est ce mécanisme qui, en production, fera
        # refuser le démarrage (cf. verifier_securite dans main.py). On importe le
        # module pour comparer directement la clé active à la valeur par défaut.
        from src.api import auth
        assert auth.CLE_SECRETE == auth.SECRET_PAR_DEFAUT
        assert secret_est_faible() is True