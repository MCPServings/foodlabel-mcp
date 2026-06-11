"""框架无关的核心：食品标签 → 视觉模型识读 → 国标合规判定 → 规范化报告.

本模块不依赖任何 Web 框架，便于被 FastAPI 后端与 MCP server 共同复用：

  * FastAPI 后端（server/app.py）解析 multipart 后调用本模块。
  * MCP server 直接以 data URL / base64 调用本模块。

校验类错误统一抛 InputError（上层映射为 400）；模型/网络错误由 llm.LLMError
向上抛（上层映射为 502）。
"""
from __future__ import annotations

from . import llm
from .standards import CHECKLIST, system_prompt

# 允许的图片 MIME 类型。
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
DEFAULT_MAX_IMAGES = 4
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

_CHECK_IDS = [c["id"] for c in CHECKLIST]
_ITEM_BY_ID = {c["id"]: c["item"] for c in CHECKLIST}
_BASIS_BY_ID = {c["id"]: c["basis"] for c in CHECKLIST}
_CAT_BY_ID = {c["id"]: c["category"] for c in CHECKLIST}


class InputError(ValueError):
    """用户输入问题（图片缺失 / 过大 / 格式不支持）。上层应映射为 HTTP 400。"""


async def check_image_bytes(
    items: list[tuple[bytes, str | None]],
    *,
    max_images: int = DEFAULT_MAX_IMAGES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_types: set[str] | None = None,
) -> dict:
    """从原始图片字节开始的完整检查流程，返回规范化后的合规报告。"""
    allowed = allowed_types or ALLOWED_TYPES
    if not items:
        raise InputError("请至少上传一张标签图片。")
    if len(items) > max_images:
        raise InputError(f"最多一次上传 {max_images} 张图片。")

    data_urls: list[str] = []
    for raw, ctype in items:
        if not raw:
            continue
        if len(raw) > max_bytes:
            raise InputError(f"单张图片不能超过 {max_bytes // (1024 * 1024)} MB。")
        ct = (ctype or "").lower()
        if ct and ct not in allowed:
            raise InputError(f"不支持的图片格式：{ct}")
        data_urls.append(llm.prepare_image(raw, ct or None))

    if not data_urls:
        raise InputError("上传的图片为空。")
    return await analyze_data_urls(data_urls)


async def analyze_data_urls(data_urls: list[str]) -> dict:
    """从已就绪的 data URL 列表开始，调用模型并规范化结果。"""
    if not data_urls:
        raise InputError("没有可分析的图片。")
    result = await llm.analyze(data_urls, system_prompt())
    return normalize(result)


def normalize(result: dict) -> dict:
    """对模型输出做轻量校正：补全检查项与计数，保证字段存在，便于稳定渲染。"""
    if not isinstance(result, dict):
        return {"error": "模型返回格式异常。"}
    checks = result.get("checks") or []
    if not isinstance(checks, list):
        checks = []
    seen = {c.get("id") for c in checks if isinstance(c, dict)}
    # 模型漏判的检查项补成 unknown，保证清单完整。
    for cid in _CHECK_IDS:
        if cid not in seen:
            checks.append(
                {
                    "id": cid,
                    "category": _CAT_BY_ID[cid],
                    "item": _ITEM_BY_ID[cid],
                    "status": "unknown",
                    "finding": "模型未给出该项判定。",
                    "basis": _BASIS_BY_ID[cid],
                }
            )
    counts = {"pass": 0, "fail": 0, "warn": 0, "na": 0, "unknown": 0}
    for c in checks:
        st = str(c.get("status", "unknown")).lower()
        if st not in counts:
            st = "unknown"
            c["status"] = "unknown"
        counts[st] += 1
    result["checks"] = checks

    summary = result.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    summary.setdefault("pass", counts["pass"])
    summary.setdefault("fail", counts["fail"])
    summary.setdefault("warn", counts["warn"])
    if not summary.get("verdict"):
        if result.get("is_food_label") is False:
            summary["verdict"] = "not_a_label"
        elif counts["fail"]:
            summary["verdict"] = "non_compliant"
        elif counts["warn"]:
            summary["verdict"] = "issues"
        else:
            summary["verdict"] = "compliant"
    result["summary"] = summary
    result.setdefault("extracted", {})
    result.setdefault("suggestions", [])
    return result
