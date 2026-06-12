"""模型客户端：OCR 与识读/评价均走硅基流动（OpenAI 兼容）.

封装两类调用：

  * ocr_all(image)        并行跑 OCR 视觉模型（硅基流动 DeepSeek-OCR）识别标签文字
  * reason_json(...)      用识读/评价模型做返回 JSON 的调用（默认硅基流动 Qwen/Qwen3-8B）

配置（环境变量）：
    SF_BASE_URL        OCR 网关，默认 https://api.siliconflow.cn/v1
    SF_API_KEY         OCR 网关 key（硅基流动，必填）
    SF_OCR_MODELS      逗号分隔的 OCR 模型，默认 deepseek-ai/DeepSeek-OCR
    SF_REASON_MODEL    识读/评价模型，默认 Qwen/Qwen3-8B
    SF_REASON_BASE_URL 识读/评价网关，默认 https://api.siliconflow.cn/v1
    SF_REASON_API_KEY  识读/评价网关 key；留空则回落用 SF_API_KEY/SF_BASE_URL
    SF_REASON_NO_THINK 关闭模型思考（Qwen3 等），默认 1（关）。关思考更快更稳。
    SF_TIMEOUT         秒，默认 120
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
        "SF_OCR_MODELS", "deepseek-ai/DeepSeek-OCR"
    ).split(",")
    if m.strip()
]
# 识读 / 评价模型。默认硅基流动 Qwen/Qwen3-8B（免费、关思考约 90s、与 OCR 同网关）。
REASON_MODEL = os.getenv("SF_REASON_MODEL", "Qwen/Qwen3-8B")
# reason 网关；默认与 OCR 同走硅基流动。缺 key 时回落 OCR 网关 base/key。
REASON_BASE_URL = os.getenv("SF_REASON_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
REASON_API_KEY = os.getenv("SF_REASON_API_KEY", "")
# 关闭模型思考（硅基流动 Qwen3 等支持 chat_template_kwargs.enable_thinking=false）。
# 关思考更快更稳；不支持的网关会忽略此参数，加了无害。
_REASON_NO_THINK = os.getenv("SF_REASON_NO_THINK", "1") == "1"
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


def _headers(api_key: str = SF_API_KEY) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
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


async def _chat(payload: dict, *, base_url: str = SF_BASE_URL, api_key: str = SF_API_KEY) -> dict:
    """带重试的 chat/completions 调用，返回 message 字典。可指定网关 base/key。"""
    last: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions", headers=_headers(api_key), json=payload
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            body = e.response.text[:200] if e.response is not None else ""
            last = LLMError(f"{payload.get('model')} 返回 {code}: {body}")
            if code not in (429, 500, 502, 503, 504, 524):
                raise last from e
        except (httpx.HTTPError, KeyError, IndexError) as e:
            last = LLMError(f"{payload.get('model')} 调用失败: {e}")
        if attempt < _RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last or LLMError("调用失败")


def _msg_text(msg: dict) -> str:
    """取 message 正文；推理模型可能把答案放在 reasoning_content。"""
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


async def _chat_stream(payload: dict, *, base_url: str, api_key: str) -> str:
    """流式 chat/completions，返回拼接后的正文 content。

    推理型模型（Qwen3.6）思考 + 正文可能耗时 ~2 分钟，非流式会因代理/CF 空闲
    超时被掐断（RemoteDisconnected / 524）。流式边生成边收，连接不空闲，稳定得多。
    只累计 content（正文），思考过程 reasoning 丢弃。
    """
    payload = {**payload, "stream": True}
    last: Exception | None = None
    for attempt in range(_RETRIES + 1):
        content_parts: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with client.stream(
                    "POST", f"{base_url}/chat/completions",
                    headers=_headers(api_key), json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        if delta.get("content"):
                            content_parts.append(delta["content"])
            text = "".join(content_parts).strip()
            if text:
                return text
            last = LLMError("流式响应未返回正文 content")
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            last = LLMError(f"{payload.get('model')} 返回 {code}")
            if code not in (429, 500, 502, 503, 504, 524):
                raise last from e
        except (httpx.HTTPError, KeyError, IndexError) as e:
            last = LLMError(f"{payload.get('model')} 流式调用失败: {e}")
        if attempt < _RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last or LLMError("流式调用失败")


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


async def reason_json(
    system: str, user: str, *, images: list[str] | None = None, max_tokens: int = 16000
) -> dict:
    """用识读/评价模型做一次返回 JSON 的调用（流式，走 reason 网关，默认硅基流动 Qwen3-8B）。

    用流式避免长响应被代理/CF 空闲超时掐断。默认关思考（_REASON_NO_THINK），
    输出更快更稳；max_tokens 上限给足以容纳较长的 JSON 报告（及可能的思考预算）。
    images（data URL 列表）仅在 reason 模型支持视觉时有效；纯文本模型（如 Qwen3-8B）请勿传。
    """
    # reason 网关未单独配 key 时回落到 OCR 网关（硅基流动）的 base/key。
    base = REASON_BASE_URL if REASON_API_KEY else SF_BASE_URL
    key = REASON_API_KEY or SF_API_KEY
    if images:
        user_content: list[dict] = [{"type": "text", "text": user}]
        for url in images:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
    else:
        user_content = user  # 纯文本
    payload = {
        "model": REASON_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if _REASON_NO_THINK:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    text = await _chat_stream(payload, base_url=base, api_key=key)
    return _parse_json(text)


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
