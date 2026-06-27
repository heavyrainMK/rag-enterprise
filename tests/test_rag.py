##############################################################
# Nom ......... : test_rag.py
# Rôle ........ : Tests du cœur RAG src/core/rag.py (contexte,
#                 recherche multi-collections, streaming).
# Auteur ...... : Maxim Khomenko
# Version ..... : V1.1.0 du 27/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_rag.py -v
# Dépendances . : pytest, unittest.mock, langchain-core, src.core.rag
##############################################################

from unittest.mock import patch, MagicMock

from langchain_core.documents import Document

from src.core import rag


def doc(contenu, source):
    # Fabrique-raccourci pour créer un Document LangChain minimal. On ne renseigne
    # que ce dont les tests ont besoin (contenu + source), ce qui garde chaque
    # test concis et lisible.
    return Document(page_content=contenu, metadata={"source": source})


def faux_vs(docs_scores):
    # Faux « vectorstore » : un mock dont la seule méthode utile,
    # similarity_search_with_score, renvoie une liste fixe de couples
    # (document, score). Cela permet de tester toute la logique de recherche SANS
    # ChromaDB ni modèle d'embedding, donc sans I/O ni lenteur, et avec des scores
    # qu'on contrôle exactement pour cibler chaque cas (seuil, tri, fusion).
    vs = MagicMock()
    vs.similarity_search_with_score.return_value = docs_scores
    return vs


class TestConstruireContexte:

    def test_sans_documents(self):
        # Aucun document = contexte vide. Garantit qu'on ne fabrique pas un bloc
        # de texte parasite quand il n'y a rien à fournir au LLM.
        assert rag.construire_contexte([]) == ""

    def test_avec_source_et_contenu(self):
        # Le contexte construit doit contenir à la fois le contenu du morceau ET
        # le nom de sa source : c'est cette présence de la source dans le texte
        # injecté qui permet au modèle de citer correctement ses références.
        ctx = rag.construire_contexte([doc("Texte du document.", "rh.pdf")])
        assert "rh.pdf" in ctx
        assert "Texte du document." in ctx

    def test_plusieurs_morceaux(self):
        # Avec plusieurs morceaux, chacun est numéroté (Extrait 1, Extrait 2...)
        # et accompagné de sa source. La numérotation aide le LLM à distinguer les
        # extraits et à ne pas les confondre.
        ctx = rag.construire_contexte([doc("Premier.", "a.txt"), doc("Second.", "b.txt")])
        assert "Extrait 1" in ctx and "Extrait 2" in ctx
        assert "a.txt" in ctx and "b.txt" in ctx


class TestRechercheMulti:

    def test_filtre_au_dessus_du_seuil(self):
        # On prépare deux morceaux : un sous le seuil (pertinent) et un nettement
        # au-dessus (hors sujet). Seul le pertinent doit être retenu. C'est le test
        # du PREMIER filtre (seuil absolu) : le bruit grossier est écarté dès la
        # recherche. patch.object remplace charger_collection pour injecter notre
        # faux vectorstore uniquement sur la collection partagée.
        bon = (doc("pertinent", "shared.txt"), rag.SEUIL_PERTINENCE - 0.1)
        mauvais = (doc("hors sujet", "shared.txt"), rag.SEUIL_PERTINENCE + 0.5)
        with patch.object(rag, "charger_collection") as charger:
            charger.side_effect = lambda nom: (
                faux_vs([bon, mauvais]) if nom == rag.COLLECTION_PARTAGEE else None
            )
            docs, prives, partages = rag.rechercher_multi("q", "alice")
        assert len(docs) == 1
        assert partages == ["shared.txt"]
        assert prives == []

    def test_fusion_partage_et_prive(self):
        # Un morceau dans le partagé, un dans le privé : les deux doivent être
        # fusionnés dans le résultat, et chacun correctement classé dans sa
        # catégorie (partagé vs privé). On valide ainsi que rechercher_multi
        # interroge bien les DEUX collections et ventile les sources par origine.
        partage = (doc("doc partagé", "shared.txt"), 0.2)
        prive = (doc("doc privé", "prive.txt"), 0.3)

        # side_effect avec une fonction : le faux charger_collection renvoie un
        # vectorstore différent selon la collection demandée, ce qui simule la
        # présence simultanée d'un document partagé et d'un document privé.
        def charger(nom):
            return faux_vs([partage]) if nom == rag.COLLECTION_PARTAGEE else faux_vs([prive])

        with patch.object(rag, "charger_collection", side_effect=charger):
            docs, prives, partages = rag.rechercher_multi("q", "alice")
        assert len(docs) == 2
        assert partages == ["shared.txt"]
        assert prives == ["prive.txt"]

    def test_tri_par_score_croissant(self):
        # Deux morceaux dans le désordre (score 0.9 puis 0.1) : après recherche,
        # le plus proche (score le plus bas) doit arriver EN PREMIER. Ce tri est
        # déterminant car le second filtre (marge d'affichage) se cale sur ce
        # meilleur score ; un mauvais ordre fausserait tout l'aval.
        loin = (doc("loin", "a.txt"), 0.9)
        proche = (doc("proche", "b.txt"), 0.1)
        with patch.object(rag, "charger_collection") as charger:
            charger.side_effect = lambda nom: (
                faux_vs([loin, proche]) if nom == rag.COLLECTION_PARTAGEE else None
            )
            docs, _, _ = rag.rechercher_multi("q", "alice")
        assert docs[0].page_content == "proche"
        assert docs[1].page_content == "loin"

    def test_aucun_resultat(self):
        # Aucune collection disponible (charger renvoie toujours None) : la
        # recherche renvoie trois listes vides. C'est ce vide qui, en amont,
        # déclenchera le garde-fou anti-hallucination (pas de contexte, pas
        # d'appel au LLM).
        with patch.object(rag, "charger_collection", return_value=None):
            docs, prives, partages = rag.rechercher_multi("q", "alice")
        assert docs == [] and prives == [] and partages == []


class TestPreparerContexte:

    def test_sans_contexte(self):
        # Quand la recherche ne ramène rien, preparer_contexte doit renvoyer un
        # ContexteRAG marqué a_du_contexte=False et un contexte vide. Ce drapeau
        # est précisément ce que testent repondre() et repondre_stream() pour
        # décider de NE PAS appeler le modèle.
        with patch.object(rag, "rechercher_multi", return_value=([], [], [])):
            ctx = rag.preparer_contexte("q", "alice")
        assert ctx.a_du_contexte is False
        assert ctx.contexte == ""

    def test_avec_contexte(self):
        # À l'inverse, avec un document trouvé : a_du_contexte=True, le contexte
        # mentionne la source, et les sources/morceaux sont correctement remplis.
        # On vérifie ici l'alignement final entre ce qui est injecté au LLM et ce
        # qui est exposé comme sources.
        docs = [doc("contenu", "shared.txt")]
        with patch.object(rag, "rechercher_multi", return_value=(docs, [], ["shared.txt"])):
            ctx = rag.preparer_contexte("q", "alice")
        assert ctx.a_du_contexte is True
        assert "shared.txt" in ctx.contexte
        assert ctx.sources == ["shared.txt"]
        assert ctx.morceaux == ["contenu"]


class TestRepondreStream:

    def test_evenement_sans_contexte(self):
        # Sans contexte, le streaming doit émettre UN SEUL événement de type
        # no_context, et surtout jamais appeler le LLM. C'est la version streaming
        # du garde-fou architectural : on répond honnêtement sans rien inventer.
        ctx_vide = rag.ContexteRAG(
            contexte="", sources=[], sources_privees=[],
            sources_partagees=[], morceaux=[], a_du_contexte=False,
        )
        with patch.object(rag, "preparer_contexte", return_value=ctx_vide):
            evenements = list(rag.repondre_stream("q", "alice"))
        assert len(evenements) == 1
        assert evenements[0]["type"] == "no_context"

    def test_tokens_puis_done(self):
        # Cas nominal du streaming. On remplace la chaîne LangChain par une fausse
        # chaîne dont stream() produit deux tokens. On vérifie alors la SÉQUENCE
        # attendue : les tokens arrivent dans l'ordre, puis un événement final
        # « done » porte les sources. C'est exactement ce que stream.py relaie au
        # client en SSE, donc ce test verrouille le contrat entre les deux.
        ctx = rag.ContexteRAG(
            contexte="contexte", sources=["shared.txt"],
            sources_privees=[], sources_partagees=["shared.txt"],
            morceaux=["contexte"], a_du_contexte=True,
        )

        # Fausse chaîne : on n'a besoin que de la méthode stream(), qui imite la
        # génération token par token sans jamais solliciter Ollama.
        class FausseChaine:
            def stream(self, _):
                yield "Bon"
                yield "jour"

        with patch.object(rag, "preparer_contexte", return_value=ctx), \
             patch.object(rag, "construire_chaine", return_value=FausseChaine()):
            evenements = list(rag.repondre_stream("q", "alice"))
        types = [e["type"] for e in evenements]
        tokens = [e["content"] for e in evenements if e["type"] == "token"]
        assert tokens == ["Bon", "jour"]
        # L'événement final doit être « done » et transporter les sources : c'est
        # lui qui permet à l'interface d'afficher les références après la réponse.
        assert types[-1] == "done"
        assert evenements[-1]["sources"] == ["shared.txt"]

    def test_erreur_recherche(self):
        # Si la préparation du contexte échoue, le streaming ne doit pas planter
        # brutalement : il émet un événement « error » dont le message reprend la
        # cause. C'est ce qui permet à stream.py de transmettre une erreur propre
        # au client plutôt que de couper la connexion sans explication.
        with patch.object(rag, "preparer_contexte", side_effect=RuntimeError("boum")):
            evenements = list(rag.repondre_stream("q", "alice"))
        assert len(evenements) == 1
        assert evenements[0]["type"] == "error"
        assert "boum" in evenements[0]["content"]