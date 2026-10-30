import io
import logging
import os
import secrets
import time
import warnings
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Depends
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from extra_totap_v2 import extract_total

logger = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_SIDE = 2200


def load_ocr():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang=os.getenv("OCR_LANG", "vi"),
        device=os.getenv("OCR_DEVICE", "cpu"),
        # Tránh lỗi ConvertPirAttribute2RuntimeAttribute của oneDNN trên môi trường này.
        enable_mkldnn=False,
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
        use_textline_orientation=True,
    )


@asynccontextmanager
async def lifespan(app):
    app.state.busy = False
    app.state.ocr = await run_in_threadpool(load_ocr)
    yield
    app.state.ocr = None


app = FastAPI(title="DontLift Receipt Scan", version="0.1.0", lifespan=lifespan)


async def authenticate(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("SCAN_API_KEY")
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(401, "Invalid API key")


def scan_image(ocr, content):
    start = time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.format not in ("JPEG", "PNG", "WEBP"):
                    raise HTTPException(415, "Chỉ hỗ trợ JPEG, PNG, WEBP")
                if source.width * source.height > MAX_PIXELS:
                    raise HTTPException(413, "Ảnh vượt quá 25 triệu pixel")
                img = ImageOps.exif_transpose(source).convert("RGB")
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                # PaddleOCR nhận mảng ảnh theo thứ tự màu BGR.
                pixels = np.asarray(img)[:, :, ::-1].copy()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise HTTPException(400, "Ảnh không hợp lệ hoặc không giải mã được") from exc
    prepared = time.perf_counter()
    results = list(ocr.predict(pixels))
    recognized = time.perf_counter()
    if len(results) != 1:
        raise RuntimeError("Expected one OCR result for one image")
    payload = results[0].json
    data = payload.get("res", payload)
    result = extract_total(data)
    proposal = result["proposal"]
    # Không đề xuất mã số hoặc số không có nhãn tổng tiền hỗ trợ.
    if proposal and proposal["role"] not in ("total", "weak_total"):
        proposal = None
        result["amount"] = None
        result["status"] = "needs_review"
    finished = time.perf_counter()
    return {
        "status": result["status"],
        "amount": result["amount"],
        "suggested_amount": proposal["amount"] if proposal else None,
        "currency": "VND",
        "needs_confirmation": True,
        "evidence": proposal["evidence"] if proposal else None,
        "warnings": result["warnings"],
        "timings_ms": {
            "preprocess": round((prepared - start) * 1000),
            "ocr": round((recognized - prepared) * 1000),
            "extract": round((finished - recognized) * 1000),
            "total": round((finished - start) * 1000),
        },
    }


@app.get("/health")
async def health():
    return {"status": "ready", "busy": app.state.busy}


@app.post("/scan", dependencies=[Depends(authenticate)])
async def scan(image: UploadFile = File(...)):
    if app.state.busy:
        await image.close()
        raise HTTPException(503, "OCR đang bận, vui lòng thử lại", headers={"Retry-After": "3"})
    app.state.busy = True
    try:
        content = await image.read(MAX_BYTES + 1)
        if not content:
            raise HTTPException(400, "Ảnh rỗng")
        if len(content) > MAX_BYTES:
            raise HTTPException(413, "Ảnh tối đa 10 MB")
        return await run_in_threadpool(scan_image, app.state.ocr, content)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Receipt scan failed")
        raise HTTPException(500, "Không xử lý được ảnh hóa đơn") from exc
    finally:
        app.state.busy = False
        await image.close()
