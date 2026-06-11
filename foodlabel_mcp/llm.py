"""视觉大模型客户端（OpenAI 兼容）。

通过环境变量配置，指向任意 OpenAI 兼容、且支持视觉（image_url）的端点：

    LLM_BASE_URL   默认 https://tianshu-gateway.cloud/v1  （天枢网关）
    LLM_API_KEY    bearer key
    LLM_MODEL      默认 OpenAI/GPT-5.5 （视觉 + 推理；实测可读中文标签图片）
    LLM_TIMEOUT    秒，默认 120

只做一件事：把标签图片 + 系统提示词发给模型，拿回结构化 JSON 判定结果。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os

import httpx

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://tianshu-gateway.cloud/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "OpenAI/GPT-5.5")
_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
# 推理型模型（如 GPT-5.5）不接受自定义 temperature，传了会返回 500。
# 默认不传；只有显式设了 LLM_TEMPERATURE（给非推理型模型用）才加上。
_TEMPERATURE = os.getenv("LLM_TEMPERATURE", "").strip()
# 图片送模型前的长边上限（像素），控制 token 成本；0 关闭缩放。
_MAX_EDGE = int(os.getenv("LLM_IMAGE_MAX_EDGE", "1600"))
# 5xx / 超时重试次数与退避（秒）。推理型网关偶发 500。
_RETRIES = int(os.getenv("LLM_RETRIES", "2"))
_RETRY_BACKOFF = float(os.getenv("LLM_RETRY_BACKOFF", "2"))


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、上游错误等）。"""


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        h["Authorization"] = f"Bearer {LLM_API_KEY}"
    return h


def prepare_image(raw: bytes, content_type: str | None = None) -> str:
    """把图片字节转成 data URL。可用 Pillow 时顺带缩放/转 JPEG 以省 token。"""
    mime = content_type or "image/jpeg"
    data = raw
    try:
        from PIL import Image  # 可选依赖

        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        if _MAX_EDGE and max(img.size) > _MAX_EDGE:
            scale = _MAX_EDGE / max(img.size)
            img = img.resize((round(img.width * scale), round(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        mime = "image/jpeg"
    except Exception:
        # Pillow 缺失或解码失败：原样发送原始字节。
        pass
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"


async def analyze(images: list[str], system: str, *, max_tokens: int = 6000) -> dict:
    """把若干张图片（data URL）与系统提示词发给视觉模型，返回解析后的 JSON。"""
    content: list[dict] = [
        {
            "type": "text",
            "text": "这是同一件预包装食品的标签照片，请按系统指令识读并逐项判定合规性，只输出 JSON。",
        }
    ]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    # 仅在显式配置时才传 temperature（避免 GPT-5.5 等推理型模型返回 500）。
    if _TEMPERATURE:
        try:
            payload["temperature"] = float(_TEMPERATURE)
        except ValueError:
            pass

    # 推理型网关（如 GPT-5.5）偶发 5xx / 超时，做有限次重试提升稳定性。
    last_err: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{LLM_BASE_URL}/chat/completions", headers=_headers(), json=payload
                )
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                text = msg.get("content") or msg.get("reasoning_content") or ""
            return _parse_json(text)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            body = e.response.text[:300] if e.response is not None else ""
            last_err = LLMError(f"模型返回错误 {code}: {body}")
            # 仅对 5xx / 429 重试；4xx（除 429）是请求本身的问题，直接抛。
            if code not in (429, 500, 502, 503, 504):
                raise last_err from e
        except (httpx.HTTPError, KeyError, IndexError) as e:
            last_err = LLMError(f"无法连接或解析模型响应: {e}")
        except LLMError as e:
            # _parse_json 解析失败：可能是被截断的偶发输出，重试一次也无妨。
            last_err = e
        if attempt < _RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))

    raise last_err or LLMError("模型调用失败")


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
