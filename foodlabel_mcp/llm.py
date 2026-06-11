"""硅基流动（SiliconFlow）多模型客户端.

封装三类调用，全部走硅基流动 OpenAI 兼容接口：

  * ocr_all(image)        并行跑多个 OCR 视觉模型（PaddleOCR-VL-1.5 / DeepSeek-OCR）
  * reason_json(...)      用 DeepSeek-R1-0528-Qwen3-8B 做评价 / 条文比对，返回 JSON

配置（环境变量）：
    SF_BASE_URL     默认 https://api.siliconflow.cn/v1
    SF_API_KEY      硅基流动 key（必填）
    SF_OCR_MODELS   逗号分隔的 OCR 模型，默认 PaddlePaddle/PaddleOCR-VL-1.5,deepseek-ai/DeepSeek-OCR
    SF_REASON_MODEL 评价/分析模型，默认 deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
    SF_TIMEOUT      秒，默认 120
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re

import httpx

SF_BASE_URL = os.getenv("SF_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
SF_API_KEY = os.getenv("SF_API_KEY", "")
OCR_MODELS = [
    m.strip()
    for m in os.getenv(
        "SF_OCR_MODELS", "PaddlePaddle/PaddleOCR-VL-1.5,deepseek-ai/DeepSeek-OCR"
    ).split(",")
    if m.strip()
]
REASON_MODEL = os.getenv("SF_REASON_MODEL", "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B")
_TIMEOUT = float(os.getenv("SF_TIMEOUT", "120"))
# 5xx / 超时重试次数与退避（秒）。
_RETRIES = int(os.getenv("SF_RETRIES", "2"))
_RETRY_BACKOFF = float(os.getenv("SF_RETRY_BACKOFF", "2"))
# 图片送模型前的长边上限（像素），控制 token 成本；0 关闭缩放。
_MAX_EDGE = int(os.getenv("SF_IMAGE_MAX_EDGE", "1600"))

# PaddleOCR-VL 等模型会在输出里夹带 <|LOC_123|> 之类坐标 token，清洗掉。
_LOC_TOKEN = re.compile(r"<\|[A-Za-z]+_\d+\|>")


class LLMError(RuntimeError):
    """硅基流动调用失败（网络、鉴权、上游错误等）。"""


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if SF_API_KEY:
        h["Authorization"] = f"Bearer {SF_API_KEY}"
    return h


def prepare_image(raw: bytes, content_type: str | None = None) -> str:
    """把图片字节转成 data URL。可用 Pillow 时顺带缩放/转 JPEG 以省 token。"""
    mime = content_type or "image/jpeg"
    data = raw
    try:
        from PIL import Image  # 可选依赖

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if _MAX_EDGE and max(img.size) > _MAX_EDGE:
            scale = _MAX_EDGE / max(img.size)
            img = img.resize((round(img.width * scale), round(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        data = buf.getvalue()
        mime = "image/jpeg"
    except Exception:
        # Pillow 缺失或解码失败：原样发送原始字节。
        pass
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def _chat(payload: dict) -> dict:
    """带重试的 chat/completions 调用，返回 message 字典。"""
    last: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{SF_BASE_URL}/chat/completions", headers=_headers(), json=payload
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            body = e.response.text[:200] if e.response is not None else ""
            last = LLMError(f"{payload.get('model')} 返回 {code}: {body}")
            if code not in (429, 500, 502, 503, 504):
                raise last from e
        except (httpx.HTTPError, KeyError, IndexError) as e:
            last = LLMError(f"{payload.get('model')} 调用失败: {e}")
        if attempt < _RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last or LLMError("调用失败")


def _msg_text(msg: dict) -> str:
    """取 message 正文；推理模型可能把答案放在 reasoning_content。"""
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


async def ocr(model: str, image_data_url: str, *, max_tokens: int = 3000) -> str:
    """对单张图片做 OCR，返回纯文本（清洗坐标 token）。"""
    content = [
        {
            "type": "text",
            "text": "请识别图片中的所有文字，按版面顺序逐行原样输出，保留数字、单位与标点，"
            "不要翻译、不要解释、不要输出坐标或任何额外标记。",
        },
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]
    msg = await _chat(
        {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
    )
    return _LOC_TOKEN.sub("", _msg_text(msg)).strip()


async def ocr_all(image_data_url: str) -> list[dict]:
    """并行跑所有 OCR 模型，返回 [{model, text, error}]。"""

    async def one(m: str) -> dict:
        try:
            return {"model": m, "text": await ocr(m, image_data_url), "error": None}
        except LLMError as e:
            return {"model": m, "text": "", "error": str(e)}

    return list(await asyncio.gather(*(one(m) for m in OCR_MODELS)))


async def reason_json(system: str, user: str, *, max_tokens: int = 4000) -> dict:
    """用推理模型做一次返回 JSON 的调用。"""
    msg = await _chat(
        {
            "model": REASON_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
    )
    return _parse_json(_msg_text(msg))


def _parse_json(text: str) -> dict:
    """尽力从模型输出中解析出 JSON 对象。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("模型未返回有效 JSON")
