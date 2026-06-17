# foodlabel-mcp

把"食品标签图片 → 视觉大模型识读 → 中国国家标准合规判定"封装为 **MCP server**，
供 Claude Desktop、IDE 等任意 MCP 客户端调用。

依据标准（均于 **2027-03-16** 实施）：

- **GB 7718-2025**《食品安全国家标准 预包装食品标签通则》
- **GB 28050-2025**《食品安全国家标准 预包装食品营养标签通则》

> 结果由 AI 识图后自动比对生成，仅供参考，不构成官方监管结论或法律意见。

这是 [foodlabel](https://github.com/AlisaLi0/foodlabel)（Web 前后端）的 MCP 封装，
复用同一套框架无关核心（`standards.py` / `llm.py` / `core.py`）。

## 工具

| 工具 | 说明 |
|---|---|
| `check_food_label(images)` | 对一张/多张同一商品的标签图片做合规检查，返回结构化报告（识读字段、营养成分表、逐项判定、整改建议）。`images` 每项可为 data URL、http(s) 图片 URL，或纯 base64。 |
| `get_checklist()` | 返回强制检查清单与标准依据。 |

## 安装运行

```bash
pip install -e .                 # 或 pip install -r requirements.txt
cp .env.example .env             # 填 LLM_API_KEY
export $(grep -v '^#' .env | xargs)
foodlabel-mcp                    # stdio（默认）
```

## 接入 Claude Desktop

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "foodlabel": {
      "command": "foodlabel-mcp",
      "env": {
        "LLM_BASE_URL": "https://tianshu-gateway.cloud/v1",
        "LLM_API_KEY": "sk-...",
        "LLM_MODEL": "OpenAI/GPT-5.5"
      }
    }
  }
}
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://tianshu-gateway.cloud/v1` | OpenAI 兼容、支持视觉的网关 |
| `LLM_API_KEY` | （必填） | 网关 bearer key |
| `LLM_MODEL` | `OpenAI/GPT-5.5` | 视觉模型 |
| `LLM_TEMPERATURE` | 空 | 推理型模型留空；非推理型可设 0.1 |
| `MCP_TRANSPORT` | `stdio` | `stdio` / `sse` / `streamable-http` |

---

## License

MIT © MCPServings. See [LICENSE](LICENSE).
