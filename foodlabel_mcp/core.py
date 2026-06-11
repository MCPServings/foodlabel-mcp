"""框架无关的核心：食品标签 → 双 OCR 识别 → R1 评价合并 → R1 国标比对 → 规范化报告.

本模块不依赖任何 Web 框架，便于被 FastAPI 后端与 MCP server 共同复用：

  * FastAPI 后端（server/app.py）解析 multipart 后调用本模块。
  * MCP server 直接以 data URL / base64 调用本模块。

流程：
  1) PaddleOCR-VL-1.5 与 DeepSeek-OCR 并行识别图片文字；
  2) DeepSeek-R1-0528-Qwen3-8B 评价各 OCR 结果质量并融合出最佳文本；
  3) DeepSeek-R1 把融合文本逐条对照 GB 7718-2025 / GB 28050-2025，
     输出 checks 与 missing / problems / risks（缺失/问题/风险点）。

校验类错误统一抛 InputError（上层映射为 400）；模型/网络错误由 llm.LLMError
向上抛（上层映射为 502）。
"""
from __future__ import annotations

from . import llm
from .standards import CHECKLIST, analyze_system, eval_system

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
    """三段式流程：双 OCR 并行 → R1 评价融合 → R1 国标比对 → 规范化。"""
    if not data_urls:
        raise InputError("没有可分析的图片。")

    # 1) 多 OCR 模型并行识别（对每张图都跑；多图时各图结果按模型拼接）。
    per_image = []
    for url in data_urls:
        per_image.append(await llm.ocr_all(url))
    # 汇总成 [{model, text, error}]，多图时同模型文本用分隔符拼接。
    ocr_results: list[dict] = []
    for idx, model in enumerate(llm.OCR_MODELS):
        texts, errs = [], []
        for img_res in per_image:
            r = next((x for x in img_res if x["model"] == model), None)
            if not r:
                continue
            if r.get("error"):
                errs.append(r["error"])
            if r.get("text"):
                texts.append(r["text"])
        ocr_results.append(
            {
                "model": model,
                "text": "\n\n---\n\n".join(texts),
                "error": "; ".join(errs) if errs and not texts else None,
            }
        )

    if not any(r["text"] for r in ocr_results):
        # 所有 OCR 都失败：直接报错，附带各自的错误。
        detail = "; ".join(f"{r['model']}: {r['error']}" for r in ocr_results if r.get("error"))
        raise llm.LLMError(f"OCR 全部失败：{detail or '无文本'}")

    # 2) R1 评价各 OCR 结果并融合最佳文本。
    eval_user = "以下是同一张食品标签图片的多个 OCR 识别结果：\n\n" + "\n\n".join(
        f"【{r['model']}】\n{r['text'] or '(无输出' + (' / ' + r['error'] if r.get('error') else '') + ')'}"
        for r in ocr_results
    )
    evaluation = await llm.reason_json(eval_system(), eval_user, max_tokens=4000)
    merged_text = (evaluation.get("merged_text") or "").strip()
    if not merged_text:
        # 评价模型没给融合文本时，退而用最长的一份 OCR 文本。
        merged_text = max((r["text"] for r in ocr_results), key=len, default="")

    # 3) R1 对融合文本逐条对照国标，输出 checks 与 缺失/问题/风险点。
    analyze_user = "以下是某预包装食品标签识别出的文本，请逐条对照国家标准进行合规分析：\n\n" + merged_text
    analysis = await llm.reason_json(analyze_system(), analyze_user, max_tokens=6000)

    result = normalize(analysis)
    # 附上 OCR 原文与评价，供前端展示「识别过程」。
    result["ocr_results"] = ocr_results
    result["ocr_evaluation"] = {
        "evaluations": evaluation.get("evaluations", []),
        "confidence": evaluation.get("confidence"),
    }
    result["merged_text"] = merged_text
    return result



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
    # 三类问题点：缺失 / 问题 / 风险。保证为列表，便于前端稳定渲染。
    for key in ("missing", "problems", "risks"):
        v = result.get(key)
        result[key] = v if isinstance(v, list) else []
    result.setdefault("suggestions", [])
    return result
