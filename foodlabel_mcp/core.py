"""框架无关的核心：食品标签 → OCR 识别 → 文本识读 → 适用规则 → 国标比对 → 规范化报告.

本模块不依赖任何 Web 框架，便于被 FastAPI 后端与 MCP server 共同复用：

  * FastAPI 后端（server/app.py）解析 multipart 后调用本模块。
  * MCP server 直接以 data URL / base64 调用本模块。

流程（analyze_steps 分步生成器）：
  1) DeepSeek-OCR 识别图片文字；
  2) Qwen3-8B 据 OCR 文本识读出结构化字段与营养成分表；
  3) Qwen3-8B 受限分类出食品类目 → 代码确定性映射各项适用/豁免；
  4) Qwen3-8B 基于适用规则逐条对照 GB 7718-2025 / GB 28050-2025，
     输出 checks 与 missing / problems / risks（缺失/问题/风险点）。

校验类错误统一抛 InputError（上层映射为 400）；模型/网络错误由 llm.LLMError
向上抛（上层映射为 502）。
"""
from __future__ import annotations

import json

from . import llm
from .standards import (
    CHECKLIST,
    analyze_system,
    applicable_for,
    category_info,
    extract_system,
    rules_system,
)

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
    """从原始图片字节开始的完整检查流程，返回规范化后的合规报告（非流式）。"""
    data_urls = prepare_items(items, max_images=max_images, max_bytes=max_bytes, allowed_types=allowed_types)
    return await analyze_data_urls(data_urls)


def prepare_items(
    items: list[tuple[bytes, str | None]],
    *,
    max_images: int = DEFAULT_MAX_IMAGES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_types: set[str] | None = None,
) -> list[str]:
    """校验图片字节并转成 data URL 列表（校验失败抛 InputError）。"""
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
    return data_urls


async def analyze_steps(data_urls: list[str]):
    """分步异步生成器：逐步产出阶段事件，供 SSE 流式逐步返回。

    依次 yield 形如 {"step": <编号>, "stage": <名>, "status": started|done, ...} 的事件：
      1 ocr      OCR 识别（DeepSeek-OCR）
      2 extract  视觉识读字段 + 营养表（Qwen3.6 看图提取）
      3 analyze  对照国标合规分析（Qwen3.6 基于字段判定）
      4 done     汇总最终报告
    每个 done 事件携带该步的数据；前端据此点亮进度条并逐步渲染。
    """
    if not data_urls:
        raise InputError("没有可分析的图片。")

    # ── 步骤 1：OCR 识别 ──
    yield {"step": 1, "stage": "ocr", "status": "started", "label": "识别图片"}
    per_image = [await llm.ocr_all(url) for url in data_urls]
    ocr_results: list[dict] = []
    for model in llm.OCR_MODELS:
        texts, errs = [], []
        for img_res in per_image:
            r = next((x for x in img_res if x["model"] == model), None)
            if not r:
                continue
            if r.get("error"):
                errs.append(r["error"])
            if r.get("text"):
                texts.append(r["text"])
        ocr_results.append({
            "model": model,
            "text": "\n\n---\n\n".join(texts),
            "error": "; ".join(errs) if errs and not texts else None,
        })
    ocr_draft = "\n\n".join(r["text"] for r in ocr_results if r["text"])
    yield {"step": 1, "stage": "ocr", "status": "done", "ocr_results": ocr_results}

    # ── 步骤 2：基于 OCR 文本识读字段 + 营养表 ──
    yield {"step": 2, "stage": "extract", "status": "started", "label": "识读内容"}
    if not ocr_draft:
        raise llm.LLMError("OCR 未识别出任何文本，无法识读标签字段。")
    extract_user = (
        "以下是某食品标签由 OCR 识别出的文本，请据此整理出结构化字段 JSON：\n\n"
        + ocr_draft
    )
    extracted_doc = await llm.reason_json(extract_system(), extract_user)
    extracted = extracted_doc.get("extracted") or {}
    is_label = extracted_doc.get("is_food_label")
    label_type = extracted_doc.get("label_type", "")
    yield {
        "step": 2, "stage": "extract", "status": "done",
        "is_food_label": is_label, "label_type": label_type,
        "extracted": extracted, "ocr_results": ocr_results,
    }

    # 明显不是食品标签：直接收尾，不做合规分析。
    if is_label is False:
        result = normalize({
            "is_food_label": False, "label_type": label_type,
            "extracted": extracted, "checks": [],
            "summary": {"verdict": "not_a_label", "score": 0},
        })
        result["ocr_results"] = ocr_results
        yield {"step": 5, "stage": "done", "status": "done", "result": result}
        return

    # ── 步骤 3：判定适用规则（LLM 只做受限分类 → 代码确定性映射出适用条目）──
    yield {"step": 3, "stage": "rules", "status": "started", "label": "判定适用规则"}
    rules_user = "以下是某预包装食品标签已识读出的结构化字段（JSON）：\n\n" + json.dumps(
        {"label_type": label_type, "extracted": extracted}, ensure_ascii=False
    )
    rules_doc = await llm.reason_json(rules_system(), rules_user)
    category_id = rules_doc.get("category_id") or "general"
    is_import = bool(rules_doc.get("is_import"))
    scope = "import" if is_import else "domestic"
    # 适用条目由代码按固定映射确定性算出（非 LLM 自由判断）。
    applicable = applicable_for(category_id, scope)
    cat = category_info(category_id)
    rules_meta = {
        "category_id": category_id,
        "category_name": cat.get("name", ""),
        "category_basis": cat.get("basis", ""),
        "category_reason": rules_doc.get("category_reason", ""),
        "is_import": is_import,
        "applicable": [
            {"id": cid, "item": _ITEM_BY_ID[cid], "applicable": a["applicable"],
             "reason": a["reason"], "basis": a["basis"]}
            for cid, a in applicable.items()
        ],
    }
    yield {"step": 3, "stage": "rules", "status": "done", "rules": rules_meta}

    # ── 步骤 4：基于适用规则做合规评价（缺失/问题/风险）──
    yield {"step": 4, "stage": "analyze", "status": "started", "label": "合规评价"}
    applicable_text = "\n".join(
        f"- {a['id']}（{_ITEM_BY_ID[a['id']]}）：{'适用' if a['applicable'] else '不适用→判 na'}（{a['reason']}）"
        for a in applicable_list(applicable)
    )
    analyze_user = (
        "食品类目：" + cat.get("name", "") + "（" + cat.get("basis", "") + "）\n"
        "适用规则（不适用项一律判 na，不计入缺失/问题）：\n" + applicable_text + "\n\n"
        "已识读出的结构化字段（JSON）：\n" + json.dumps(
            {"label_type": label_type, "extracted": extracted}, ensure_ascii=False
        )
    )
    analysis = await llm.reason_json(analyze_system(), analyze_user)
    analysis.setdefault("extracted", extracted)
    analysis.setdefault("is_food_label", is_label)
    analysis.setdefault("label_type", label_type)
    result = normalize(analysis, applicable=applicable)
    result["ocr_results"] = ocr_results
    result["rules"] = rules_meta

    # ── 步骤 5：汇总报告 ──
    yield {"step": 5, "stage": "done", "status": "done", "result": result}


async def analyze_data_urls(data_urls: list[str]) -> dict:
    """非流式封装：跑完分步流程，返回最终报告（供 MCP / 一次性调用）。"""
    if not data_urls:
        raise InputError("没有可分析的图片。")
    final: dict | None = None
    async for ev in analyze_steps(data_urls):
        if ev.get("stage") == "done":
            final = ev.get("result")
    if final is None:
        raise llm.LLMError("分析未产出结果。")
    return final


def applicable_list(applicable: dict[str, dict]) -> list[dict]:
    """把 applicable_for 的 dict 转成保持 CHECKLIST 顺序的列表。"""
    return [{"id": c["id"], **applicable[c["id"]]} for c in CHECKLIST if c["id"] in applicable]


def normalize(result: dict, applicable: dict[str, dict] | None = None) -> dict:
    """对模型输出做轻量校正：补全检查项与计数，保证字段存在，便于稳定渲染。

    若给出 applicable（适用规则映射），则**确定性地**把不适用项强制判为 na，
    覆盖 LLM 的判定，保证适用范围严格符合国标条款。
    """
    if not isinstance(result, dict):
        return {"error": "模型返回格式异常。"}
    checks = result.get("checks") or []
    if not isinstance(checks, list):
        checks = []
    seen = {c.get("id") for c in checks if isinstance(c, dict)}
    # 模型只列出有问题/不适用的项；未列出的视为合规（pass）。
    # 但若整图不是食品标签，则补成 unknown（无从判定）。
    fill_status = "unknown" if result.get("is_food_label") is False else "pass"
    fill_finding = "模型未列为问题项。" if fill_status == "pass" else "非食品标签，无法判定。"
    for cid in _CHECK_IDS:
        if cid not in seen:
            checks.append(
                {
                    "id": cid,
                    "category": _CAT_BY_ID[cid],
                    "item": _ITEM_BY_ID[cid],
                    "status": fill_status,
                    "finding": fill_finding,
                    "basis": _BASIS_BY_ID[cid],
                }
            )
    # 确定性应用适用规则：不适用项强制 na（覆盖 LLM）。
    if applicable:
        for c in checks:
            rule = applicable.get(c.get("id"))
            if rule and not rule.get("applicable", True):
                c["status"] = "na"
                c["finding"] = rule.get("reason", "该项对本商品不适用")
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
