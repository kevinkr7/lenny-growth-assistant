import asyncio
import threading

from app.config import settings

_model = None
_lock = threading.Lock()


def _embed(texts: list[str]) -> list[list[float]]:
    global _model
    from fastembed import TextEmbedding

    with _lock:
        if _model is None:
            _model = TextEmbedding(
                model_name=settings().embedding_model, cache_dir=settings().embedding_cache_dir, threads=4
            )
        vectors = [v.tolist() for v in _model.embed(texts, batch_size=32)]
    if any(len(v) != 384 for v in vectors):
        raise ValueError("Configured embedding model must produce 384 dimensions; reindex after a model change")
    return vectors


async def embed(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_embed, texts)
