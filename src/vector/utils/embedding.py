"""通用文本嵌入服务"""

import os
from pathlib import Path
from typing import Dict, List, Union
import numpy as np
from sentence_transformers import SentenceTransformer

# ── Supported embedding models ────────────────────────────────────────────────

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

EMBEDDING_MODELS: Dict[str, dict] = {
    "all-MiniLM-L6-v2": {
        "dim": 384,
        "language": "en",
        "description": "English general-purpose (default)",
    },
    "shibing624/text2vec-base-chinese": {
        "dim": 768,
        "language": "zh",
        "description": "Chinese text embedding",
    },
    "paraphrase-multilingual-MiniLM-L12-v2": {
        "dim": 384,
        "language": "multilingual",
        "description": "Multilingual 50+ languages",
    },
}


def _find_cached_model(model_name: str) -> str:
    """Return local snapshot path if model is cached, otherwise return model_name for remote download."""
    cache_dir = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"

    # Try exact org/model format (e.g. shibing624/text2vec-base-chinese → models--shibing624--text2vec-base-chinese)
    sanitized = model_name.replace("/", "--")
    for prefix in (sanitized, f"sentence-transformers--{sanitized}"):
        model_dir = cache_dir / f"models--{prefix}" / "snapshots"
        if model_dir.exists():
            snapshots = [d for d in model_dir.iterdir() if d.is_dir()]
            if snapshots:
                return str(snapshots[0])
    return model_name  # Fall back to remote download


class EmbeddingModel:
    """文本嵌入模型封装

    使用 SentenceTransformer 将文本编码为向量表示。

    Args:
        model_name: 模型名称，默认 "all-MiniLM-L6-v2"

    Example:
        >>> model = EmbeddingModel()
        >>> embeddings = model.encode(["hello world", "goodbye"])
        >>> embeddings.shape
        (2, 384)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name

        # Load from local cache path directly to avoid any HF Hub network requests
        local_path = _find_cached_model(model_name)
        self.model = SentenceTransformer(local_path)

    def encode(self, texts: Union[str, List[str]], show_progress: bool = True) -> np.ndarray:
        """将文本编码为归一化向量

        Args:
            texts: 单个文本或文本列表
            show_progress: 是否显示进度条

        Returns:
            形状为 (n, dim) 的向量数组
        """
        return self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=show_progress
        )
