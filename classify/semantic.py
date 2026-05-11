"""
Semantic similarity scoring for job listings.

Pipeline position: used inside STAGE 2 scoring (classify/rules.py), alongside
the existing keyword-based criteria. Runs fully locally — no API calls.

Current implementation
----------------------
  Model:   sentence-transformers/all-MiniLM-L6-v2  (80 MB, CPU-fast)
  Storage: in-memory numpy array — no database required
  Similarity: cosine

The public interface is SemanticScorer.score(listing) → (float, str).
Swapping the model or adding a persistent vector store (ChromaDB, LanceDB,
FAISS) means replacing the internals of this class while leaving all callers
unchanged.

Reference embedding
-------------------
Built from criteria["role_fit"]["exact_archetypes"] by default.
Additional reference texts (e.g. profile narrative, CV summary) can be added
via SemanticScorer.add_reference_texts() before scoring begins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from classify.rules import RawListing

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class SemanticScorer:
    """
    Scores a job listing by cosine similarity to the candidate's reference embedding.

    The reference embedding is the mean of all reference texts (role archetypes,
    or any additional texts added via add_reference_texts). The mean captures
    the centroid of the candidate's target space rather than anchoring to a
    single phrase.
    """

    def __init__(self, reference_texts: list[str], model_name: str = DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # lazy import — slow to load

        self._model = SentenceTransformer(model_name)
        self._model_name = model_name
        self._reference: np.ndarray = self._mean_embedding(reference_texts)

    # ------------------------------------------------------------------
    # Public API — stable interface for callers
    # ------------------------------------------------------------------

    def score(self, listing: RawListing) -> tuple[float, str]:
        """
        Return (cosine_similarity, reason_string) for one listing.

        The text scored is title + description (if fetched). When there is no
        description, the score is based on the title only and is slightly
        penalised to reflect uncertainty.
        """
        has_desc = bool(listing.description)
        text = f"{listing.title}. {listing.description}" if has_desc else listing.title

        vec = self._model.encode(text, show_progress_bar=False)
        similarity = float(_cosine(vec, self._reference))
        similarity = max(0.0, min(1.0, similarity))

        suffix = "" if has_desc else " (title only)"
        return similarity, f"semantic similarity {similarity:.2f}{suffix}"

    def add_reference_texts(self, texts: list[str]) -> None:
        """
        Expand the reference embedding with additional texts and recompute the mean.

        Use this to fold in CV narrative, profile superpowers, or any other
        candidate signal before scoring begins.
        """
        new_vecs = self._model.encode(texts, show_progress_bar=False)
        combined = np.vstack([self._reference[np.newaxis, :], new_vecs])
        self._reference = combined.mean(axis=0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mean_embedding(self, texts: list[str]) -> np.ndarray:
        if not texts:
            raise ValueError("SemanticScorer requires at least one reference text.")
        vecs = self._model.encode(texts, show_progress_bar=False)
        return vecs.mean(axis=0)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


# ---------------------------------------------------------------------------
# Factory — build a SemanticScorer from a loaded criteria dict
# ---------------------------------------------------------------------------

def scorer_from_criteria(criteria: dict, model_name: str = DEFAULT_MODEL) -> SemanticScorer:
    """
    Build a SemanticScorer from the role_fit.exact_archetypes in a criteria dict.

    This is the standard entry point used by the pipeline. The model name can
    be overridden via criteria["semantic"]["model"] when you want a different
    model without changing Python code.
    """
    model = criteria.get("semantic", {}).get("model", model_name)
    archetypes: list[str] = criteria.get("role_fit", {}).get("exact_archetypes", [])
    if not archetypes:
        raise ValueError(
            "criteria.role_fit.exact_archetypes is empty — "
            "cannot build semantic scorer without reference texts. "
            "Run generate-criteria first."
        )
    return SemanticScorer(archetypes, model_name=model)
