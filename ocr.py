"""
图片 OCR 模块 - 基于 PaddleOCR（GPU 加速，自动检测）。

特性：
- 懒加载 PaddleOCR 实例（首次调用才下载/加载模型）
- 自动检测 GPU；有 GPU 用 GPU，否则 CPU；GPU 初始化失败自动回退 CPU
- 失败降级：返回空字符串，不中断 pipeline
- 依赖缺失：is_available() → False，调用方按需短路

依赖（不在 requirements.txt，需手动安装）：
    pip install paddlepaddle-gpu==2.6.2 "paddleocr<3.0.0" \\
        "nvidia-cudnn-cu11==8.6.0.163" nvidia-cublas-cu11 \\
        nvidia-cuda-nvrtc-cu11 nvidia-cuda-runtime-cu11 Pillow numpy \\
        -i https://pypi.tuna.tsinghua.edu.cn/simple

部署一次性操作（cuDNN 8.6 wheel 解压 + 无版本号软链）：
    mkdir -p ~/.local/cudnn8/lib
    pip download "nvidia-cudnn-cu11==8.6.0.163" --no-deps -d /tmp/cd \\
        -i https://pypi.tuna.tsinghua.edu.cn/simple
    unzip -j /tmp/cd/nvidia_cudnn_cu11-8.6.0.163-*.whl "nvidia/cudnn/lib/*" -d ~/.local/cudnn8/lib
    ln -sf libcudnn.so.8 ~/.local/cudnn8/lib/libcudnn.so
    # cuBLAS 同样需要无版本号软链：
    ln -sf libcublas.so.11 ~/.local/lib/python3.12/site-packages/nvidia/cublas/lib/libcublas.so
    ln -sf libcublasLt.so.11 ~/.local/lib/python3.12/site-packages/nvidia/cublas/lib/libcublasLt.so

运行环境：LD_LIBRARY_PATH 必须在进程启动前由调用方设置（manage.py / systemd unit 已自动注入）
指向 cuDNN/cuBLAS/nvrtc/cudart 的 lib 目录。python 内 os.environ 改动对 ld.so 无效。
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from config import (
    HEADERS,
    OCR_DOWNLOAD_WORKERS,
    OCR_LANG,
    OCR_MAX_IMAGE_BYTES,
    OCR_MAX_IMAGES_PER_NOTE,
    OCR_REQUEST_TIMEOUT_S,
)

_ocr = None
_use_gpu = False
_init_done = False  # 防止 _detect_gpu 报错时反复打 log


def is_available() -> bool:
    """OCR 依赖是否就绪（paddleocr + paddle + numpy + PIL）。"""
    try:
        import paddleocr  # noqa: F401
        import paddle  # noqa: F401
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _detect_gpu() -> bool:
    """检测 paddle 是否能用 CUDA。失败时返回 False（CPU 降级）。"""
    global _init_done
    if _init_done:
        return _use_gpu
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda():
            try:
                dev = paddle.device.get_device()
                _use_gpu = dev.startswith("gpu") or dev.startswith("cuda")
            except Exception:
                _use_gpu = False
        else:
            _use_gpu = False
    except Exception as e:
        print(f"  [OCR] GPU 检测失败，降级 CPU: {type(e).__name__}: {e}", file=sys.stderr)
        _use_gpu = False
    finally:
        _init_done = True
    return _use_gpu


def _get_ocr():
    """懒加载 PaddleOCR 实例。GPU 可用则用 GPU，初始化失败回退 CPU。"""
    global _ocr, _use_gpu
    if _ocr is None:
        from paddleocr import PaddleOCR
        _use_gpu = _detect_gpu()

        # GPU 优先；cuDNN/cuBLAS 缺失（LD_LIBRARY_PATH 未配）时自动回退 CPU
        attempts = [True, False] if _use_gpu else [False]
        for gpu_flag in attempts:
            try:
                _ocr = PaddleOCR(
                    lang=OCR_LANG,
                    show_log=False,
                    use_angle_cls=False,
                    use_gpu=gpu_flag,
                )
                _use_gpu = gpu_flag
                break
            except Exception as e:
                if gpu_flag:
                    print(
                        f"  [OCR] GPU 初始化失败，回退 CPU: {type(e).__name__} "
                        f"（检查 LD_LIBRARY_PATH 是否含 cuDNN/cuBLAS/nvrtc/cudart）",
                        file=sys.stderr,
                    )
                    _use_gpu = False
                    continue
                raise

        print(f"  [OCR] 初始化完成，设备: {'GPU' if _use_gpu else 'CPU'}")
    return _ocr


def _download_and_ocr(url: str, headers: dict) -> str:
    """下载单张图并 OCR，返回拼接文本。失败返回空串。"""
    try:
        import numpy as np
        import requests
        from PIL import Image

        resp = requests.get(
            url,
            timeout=OCR_REQUEST_TIMEOUT_S,
            headers={**headers, "Accept": "image/avif,image/webp,*/*"},
        )
        resp.raise_for_status()
        if len(resp.content) > OCR_MAX_IMAGE_BYTES:
            return ""
        img = np.array(Image.open(BytesIO(resp.content)).convert("RGB"))
        ocr = _get_ocr()
        if ocr is None:
            return ""
        result = ocr.ocr(img, cls=False)
        if not result or not result[0]:
            return ""
        lines = [ln[1][0] for ln in result[0] if ln[1][1] > 0.5]
        return "\n".join(lines)
    except Exception as e:
        print(f"  [OCR 跳过] {url[:60]}: {type(e).__name__}", file=sys.stderr)
        return ""


def ocr_images(urls: list[str], max_images: int | None = None) -> str:
    """
    对图片 URL 列表做 OCR，返回拼接文本（多图用空行分隔）。

    - 依赖缺失 → 返回空串（不抛错）
    - 并行下载（max_workers=OCR_DOWNLOAD_WORKERS）
    - 单图失败/超时不影响其他图
    - 最多取前 max_images 张（默认 OCR_MAX_IMAGES_PER_NOTE）
    """
    if not is_available():
        return ""
    urls = [u for u in urls if u][: max_images or OCR_MAX_IMAGES_PER_NOTE]
    if not urls:
        return ""

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Referer": HEADERS["Referer"],
    }

    with ThreadPoolExecutor(max_workers=OCR_DOWNLOAD_WORKERS) as ex:
        futures = {ex.submit(_download_and_ocr, u, headers): u for u in urls}
        texts: list[str] = []
        for f in futures:
            try:
                t = f.result(timeout=OCR_REQUEST_TIMEOUT_S + 5)
                if t:
                    texts.append(t)
            except Exception:
                continue
    return "\n\n".join(texts)
