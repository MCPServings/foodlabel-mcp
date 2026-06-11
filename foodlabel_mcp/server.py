"""foodlabel-mcp — 食品标签国标合规检查 MCP server.

把"食品标签图片 → 双 OCR 识别 → DeepSeek-R1 评价融合 → 对照
GB 7718-2025 / GB 28050-2025 逐条比对，输出缺失/问题/风险点"的能力
封装为 MCP 工具，供任意 MCP 客户端（Claude Desktop、IDE 等）调用。

工具：
  * check_food_label(images)  对一张/多张标签图片做合规检查，返回结构化报告
  * get_checklist()           返回强制检查清单与标准依据

图片入参支持：data URL（data:image/...;base64,xxx）、http(s) 图片 URL、或纯 base64。

配置（环境变量，与后端共用，硅基流动 SiliconFlow）：
  SF_BASE_URL     默认 https://api.siliconflow.cn/v1
  SF_API_KEY      硅基流动 key（必填）
  SF_OCR_MODELS   默认 PaddlePaddle/PaddleOCR-VL-1.5,deepseek-ai/DeepSeek-OCR
  SF_REASON_MODEL 默认 deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
传输方式：MCP_TRANSPORT=stdio（默认）| sse | streamable-http
"""
from __future__ import annotations

import base64
import os

from mcp.server.fastmcp import FastMCP

from . import core, llm
from .standards import CHECKLIST, STANDARDS

mcp = FastMCP("foodlabel-check")


def _to_image_url(x: str) -> str:
    """把单个图片入参规整成可送模型的 image_url。

    - http(s) URL：原样返回，由模型侧拉取。
    - data URL / 纯 base64：解码为字节后走 prepare_image（缩放/压缩控费）再转 data URL。
    """
    x = (x or "").strip()
    if x.startswith(("http://", "https://")):
        return x
    if x.startswith("data:"):
        try:
            b64 = x.split(",", 1)[1]
        except IndexError:
            b64 = ""
        return llm.prepare_image(base64.b64decode(b64))
    # 视为纯 base64
    return llm.prepare_image(base64.b64decode(x))


@mcp.tool()
async def check_food_label(images: list[str]) -> dict:
    """检查预包装食品标签图片是否符合中国国家标准（GB 7718-2025 标签通则、
    GB 28050-2025 营养标签通则）。

    Args:
        images: 一张或多张同一件商品的标签图片。每项可为 data URL
            （data:image/png;base64,...）、http(s) 图片 URL，或纯 base64 字符串。

    Returns:
        结构化合规报告：is_food_label、extracted（识读字段+营养成分表）、
        checks（逐项判定 pass/fail/warn/na/unknown，含标准条款 basis）、
        summary（verdict 与计数）、suggestions（整改建议）。
    """
    if not images:
        return {"error": "请至少提供一张标签图片。"}
    if len(images) > core.DEFAULT_MAX_IMAGES:
        return {"error": f"最多一次 {core.DEFAULT_MAX_IMAGES} 张图片。"}
    try:
        urls = [_to_image_url(x) for x in images]
    except Exception as e:  # base64 解码失败等
        return {"error": f"图片入参无法解析：{e}"}
    try:
        return await core.analyze_data_urls(urls)
    except llm.LLMError as e:
        return {"error": f"识别失败：{e}"}


@mcp.tool()
def get_checklist() -> dict:
    """返回食品标签合规检查所依据的强制项清单与国家标准。"""
    return {"standards": STANDARDS, "items": CHECKLIST}


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
