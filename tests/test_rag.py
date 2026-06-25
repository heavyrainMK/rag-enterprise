##############################################################
# Nom ......... : test_rag.py
# Rôle ........ : Tests du cœur RAG src/core/rag.py (contexte,
#                 recherche multi-collections, streaming).
# Auteur ...... : Maxim Khomenko
# Version ..... : V1.0.0 du 19/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_rag.py -v
# Dépendances . : pytest, unittest.mock, langchain-core, src.core.rag
##############################################################

from unittest.mock import patch, MagicMock

import pytest
from langchain_core.documents import Document

from src.core import rag


def doc(contenu, source):
    return Document(page_content=contenu, metadata={"source": source})


def faux_vs(docs_scores):
    vs = MagicMock()
    vs.similarity_search_with_score.return_value = docs_scores
    return vs


class TestConstruireContexte:

    def test_sans_documents(self):
        assert rag.construire_contexte([]) == ""

    def test_avec_source_et_contenu(self):
        ctx = rag.construire_contexte([doc("Texte du document.", "rh.pdf")])
        assert "rh.pdf" in ctx
        assert "Texte du document." in ctx

    def test_plusieurs_morceaux(self):
        ctx = rag.construire_contexte([doc("Premier.", "a.txt"), doc("Second.", "b.txt")])
        assert "Extrait 1" in ctx and "Extrait 2" in ctx
        assert "a.txt" in ctx and "b.txt" in ctx


class TestRechercheMulti:

    def test_filtre_au_dessus_du_seuil(self):
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
        partage = (doc("doc partagé", "shared.txt"), 0.2)
        prive = (doc("doc privé", "prive.txt"), 0.3)

        def charger(nom):
            return faux_vs([partage]) if nom == rag.COLLECTION_PARTAGEE else faux_vs([prive])

        with patch.object(rag, "charger_collection", side_effect=charger):
            docs, prives, partages = rag.rechercher_multi("q", "alice")
        assert len(docs) == 2
        assert partages == ["shared.txt"]
        assert prives == ["prive.txt"]

    def test_tri_par_score_croissant(self):
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
        with patch.object(rag, "charger_collection", return_value=None):
            docs, prives, partages = rag.rechercher_multi("q", "alice")
        assert docs == [] and prives == [] and partages == []


class TestPreparerContexte:

    def test_sans_contexte(self):
        with patch.object(rag, "rechercher_multi", return_value=([], [], [])):
            ctx = rag.preparer_contexte("q", "alice")
        assert ctx.a_du_contexte is False
        assert ctx.contexte == ""

    def test_avec_contexte(self):
        docs = [doc("contenu", "shared.txt")]
        with patch.object(rag, "rechercher_multi", return_value=(docs, [], ["shared.txt"])):
            ctx = rag.preparer_contexte("q", "alice")
        assert ctx.a_du_contexte is True
        assert "shared.txt" in ctx.contexte
        assert ctx.sources == ["shared.txt"]
        assert ctx.morceaux == ["contenu"]


class TestRepondreStream:

    def test_evenement_sans_contexte(self):
        ctx_vide = rag.ContexteRAG(
            contexte="", sources=[], sources_privees=[],
            sources_partagees=[], morceaux=[], a_du_contexte=False,
        )
        with patch.object(rag, "preparer_contexte", return_value=ctx_vide):
            evenements = list(rag.repondre_stream("q", "alice"))
        assert len(evenements) == 1
        assert evenements[0]["type"] == "no_context"

    def test_tokens_puis_done(self):
        ctx = rag.ContexteRAG(
            contexte="contexte", sources=["shared.txt"],
            sources_privees=[], sources_partagees=["shared.txt"],
            morceaux=["contexte"], a_du_contexte=True,
        )

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
        assert types[-1] == "done"
        assert evenements[-1]["sources"] == ["shared.txt"]

    def test_erreur_recherche(self):
        with patch.object(rag, "preparer_contexte", side_effect=RuntimeError("boum")):
            evenements = list(rag.repondre_stream("q", "alice"))
        assert len(evenements) == 1
        assert evenements[0]["type"] == "error"
        assert "boum" in evenements[0]["content"]