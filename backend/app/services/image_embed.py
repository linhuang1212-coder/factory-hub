# -*- coding: utf-8 -*-
"""本地 ONNX 图像 embedding(以图搜款)。单例加载,CPU 推理,512维 L2归一化。
同 fblerp services/image_embed，路径按工厂端目录：模型 backend/models/clip_vision_int8.onnx、
图片落 backend/uploads/。以图搜款为只读召回，绝不参与库存/对账/克重计算(铁律)。"""
import os, threading, logging, pathlib
import numpy as np
logger = logging.getLogger("app")

_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
EMBEDDING_DIM = 512
MODEL_VERSION = os.getenv("VISION_MODEL_VERSION", "fashion-clip-b32-int8")
_MODEL_PATH = os.getenv("VISION_MODEL_PATH",
    str(pathlib.Path(__file__).resolve().parent.parent.parent / "models" / "clip_vision_int8.onnx"))
_UPLOADS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "uploads"

_sess = None
_lock = threading.Lock()


def load_session():
    global _sess
    if _sess is not None:
        return _sess
    with _lock:
        if _sess is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.enable_cpu_mem_arena = False
            _sess = ort.InferenceSession(_MODEL_PATH, sess_options=so, providers=["CPUExecutionProvider"])
            logger.info(f"[vision] ONNX 已加载: {_MODEL_PATH}")
    return _sess


def is_available() -> bool:
    return pathlib.Path(_MODEL_PATH).is_file()


def _preprocess(img_bytes: bytes) -> np.ndarray:
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((224, 224), Image.BICUBIC)
    a = (np.asarray(img).astype(np.float32) / 255.0 - _MEAN) / _STD
    return np.transpose(a, (2, 0, 1))[None, :].astype(np.float32)


def embed_image_bytes(img_bytes: bytes) -> list:
    """图片字节 → 512维 L2归一化向量(list[float])。失败抛异常。"""
    sess = load_session()
    out = sess.run(None, {"pixel_values": _preprocess(img_bytes)})[0][0]
    out = out / (np.linalg.norm(out) + 1e-12)
    return out.astype(np.float32).tolist()


def embed_main_image(main_image_url: str) -> list:
    """main_image 相对URL(/uploads/styles/xxx)→ 读本地文件 → embed。"""
    if not main_image_url:
        raise ValueError("no main_image")
    rel = main_image_url.replace("/uploads/", "").lstrip("/")
    path = _UPLOADS_DIR / rel
    return embed_image_bytes(path.read_bytes())
