"""食品标签国家标准合规检查清单 + 视觉模型提示词.

本模块把两项强制性食品安全国家标准编码成结构化检查清单，供视觉大模型在
读取标签图片后逐项判定：

  * GB 7718-2025《食品安全国家标准 预包装食品标签通则》（2027-03-16 实施）
  * GB 28050-2025《食品安全国家标准 预包装食品营养标签通则》（2027-03-16 实施）

边界（硬规则）：模型只做"读取图片文字 + 对照清单判定"，不替代官方监管结论。
每一条判定都必须给出标准依据（条款号）。判定结果仅供参考，不构成法律意见。
"""
from __future__ import annotations

STANDARDS = "GB 7718-2025（预包装食品标签通则）、GB 28050-2025（预包装食品营养标签通则），均于 2027-03-16 实施"

# 强制性检查清单。每项：id / category(标准) / item(检查项) / requirement(要求摘要) / basis(条款)
# 模型对每一项返回 status ∈ {pass, fail, warn, na, unknown} 与说明。
CHECKLIST: list[dict] = [
    # ── GB 7718-2025 预包装食品标签（直接面向消费者，§4.1 一般要求）──
    {
        "id": "name",
        "category": "GB7718",
        "item": "食品名称",
        "requirement": "在醒目位置标示反映食品真实属性的名称；新创/奇特/商标等名称若易误解，须在临近位置以不大于属性名称的字体同时标示真实属性名称。",
        "basis": "GB 7718-2025 4.2",
    },
    {
        "id": "ingredients",
        "category": "GB7718",
        "item": "配料表",
        "requirement": "应有引导词“配料”或“配料表”；各配料按加入量递减排列（≤2% 可不排序）；复合配料、食品添加剂按通用名/功能类别标示。",
        "basis": "GB 7718-2025 4.3",
    },
    {
        "id": "additives",
        "category": "GB7718",
        "item": "食品添加剂标示",
        "requirement": "食品添加剂应标 GB2760/GB14880 通用名称，或“功能类别名称+通用名称/INS 号”；同一标签只选附录 B 一种形式。",
        "basis": "GB 7718-2025 4.3.4",
    },
    {
        "id": "net_content",
        "category": "GB7718",
        "item": "净含量和规格",
        "requirement": "净含量应与食品名称在同一展示版面标示（以计量方式销售的除外）；多件装应标规格。",
        "basis": "GB 7718-2025 4.5",
    },
    {
        "id": "producer",
        "category": "GB7718",
        "item": "生产者/经营者信息",
        "requirement": "应标示生产者和（或）经营者的名称、地址和联系方式。",
        "basis": "GB 7718-2025 4.6",
    },
    {
        "id": "date",
        "category": "GB7718",
        "item": "日期标示",
        "requirement": "按“年、月、日”顺序标示生产日期和保质期到期日；采用“见包装某部位”须指明具体部位；保质期≥6 个月可仅标保质期和到期日；不得加贴/补印/修改。",
        "basis": "GB 7718-2025 4.7",
    },
    {
        "id": "storage",
        "category": "GB7718",
        "item": "贮存条件",
        "requirement": "应标示贮存条件（如常温/冷藏/冷冻/避光、温湿度等）。",
        "basis": "GB 7718-2025 4.8",
    },
    {
        "id": "license",
        "category": "GB7718",
        "item": "食品生产许可证编号",
        "requirement": "国内生产销售应标示食品生产许可证编号（SC 编号）；进口食品可豁免。",
        "basis": "GB 7718-2025 4.9",
    },
    {
        "id": "std_code",
        "category": "GB7718",
        "item": "产品标准代号",
        "requirement": "国内生产并销售的预包装食品应标示所执行的产品标准代号和顺序号；进口食品可豁免。",
        "basis": "GB 7718-2025 4.10",
    },
    {
        "id": "quality_grade",
        "category": "GB7718",
        "item": "产品质量（品质）等级",
        "requirement": "所执行标准已规定质量等级的应标示，否则不得标示等级。",
        "basis": "GB 7718-2025 4.11",
    },
    {
        "id": "allergen",
        "category": "GB7718",
        "item": "致敏物质提示",
        "requirement": "八大类致敏物（含麸质谷物、甲壳类、鱼、蛋、花生、大豆、乳、坚果）如用作配料，应在配料表中或其临近位置加以提示。",
        "basis": "GB 7718-2025 4.12 / 附录 D",
    },
    {
        "id": "claims",
        "category": "GB7718",
        "item": "声称与强调用语",
        "requirement": "“无/不含”须含量为 0；不得使用“不添加/零添加”；特别强调配料须定量标示；非保健食品不得明示/暗示保健功能或防治疾病。",
        "basis": "GB 7718-2025 3.5 / 4.4",
    },
    # ── GB 28050-2025 营养标签（§4 强制内容）──
    {
        "id": "nutrition_table",
        "category": "GB28050",
        "item": "营养成分表（1+6）",
        "requirement": "以方框表强制标示：能量、蛋白质、脂肪、饱和脂肪（或饱和脂肪酸）、碳水化合物、糖、钠，共 7 项及其占 NRV 百分比。",
        "basis": "GB 28050-2025 4.1",
    },
    {
        "id": "nutrition_warning",
        "category": "GB28050",
        "item": "盐油糖提示语",
        "requirement": "营养成分表下方须标示“儿童青少年应避免过量摄入盐油糖”。",
        "basis": "GB 28050-2025 4.5",
    },
    {
        "id": "trans_fat",
        "category": "GB28050",
        "item": "反式脂肪酸",
        "requirement": "当食品或其配料使用了氢化和/或部分氢化油脂时，应标示反式脂肪酸含量。",
        "basis": "GB 28050-2025 4.4",
    },
    {
        "id": "nutrition_value_form",
        "category": "GB28050",
        "item": "含量表达方式",
        "requirement": "营养成分含量须用具体数值标示，不得用范围值（如≤XX、≥XX、X1~X2）；含量须为修约间隔整数倍。",
        "basis": "GB 28050-2025 3.4 / 6.1 / 问答(十八)",
    },
    {
        "id": "fortifier",
        "category": "GB28050",
        "item": "营养强化剂标示",
        "requirement": "使用营养强化剂时，应在营养成分表中标示强化后该营养素的含量及 NRV%。",
        "basis": "GB 28050-2025 4.3",
    },
]

# 豁免营养标签的情形（供模型在判定营养项时参考，避免误判）
NUTRITION_EXEMPTIONS = (
    "生鲜食品和粮食籽粒；单一原料干制品；包装饮用水、茶叶；酒精度>0.5%vol 饮料酒；"
    "每日食用量≤10g(mL) 的食品和单一原料调味品（食盐/味精/食醋/食糖/香辛料等）；"
    "包装最大表面面积≤40cm² 的食品。但腐乳、酱腌菜、酱油、酱类、复合调味料等不豁免。"
    "豁免营养标签者同样豁免“盐油糖”提示语。若标签明显属于豁免类别，相关营养项判 na。"
)


def system_prompt() -> str:
    """构造视觉模型的系统提示词，内含完整检查清单与输出 JSON 规范。"""
    lines = [
        "你是中国食品标签合规审查助手。用户上传一张或多张预包装食品**标签照片**"
        "（可能是正面、背面、配料/营养面等，合起来是同一件商品）。",
        f"对照现行国家标准判定其标签是否合规：{STANDARDS}。",
        "",
        "工作分两步：",
        "1) 识读：尽量准确地从图片中提取所有可见的标签文字与营养成分表数据；图片"
        "模糊或被遮挡导致看不清的字段，标为空并在该项判定中说明“图片不清/未拍到”。",
        "2) 判定：对下面每一条强制检查项给出结论。**不要臆造图片中不存在的内容**；"
        "拿不准或图片缺失就用 unknown，不要硬判 pass。",
        "",
        "每项判定的 status 取值：",
        "  pass = 标签满足该项要求；",
        "  fail = 明确违反或明确缺失该强制项；",
        "  warn = 可能存在问题或表述不规范，需人工复核；",
        "  na   = 该项对本商品不适用（如进口食品豁免许可证/标准代号、属营养标签豁免类别）；",
        "  unknown = 图片看不清或未拍到，无法判断。",
        "",
        "营养标签豁免参考：" + NUTRITION_EXEMPTIONS,
        "",
        "强制检查清单（逐项判定，basis 为标准条款，须原样回填）：",
    ]
    for c in CHECKLIST:
        lines.append(
            f"- [{c['id']}] {c['item']}（{c['basis']}）：{c['requirement']}"
        )
    lines += [
        "",
        "只输出一个 JSON 对象（不要 markdown、不要解释文字），结构如下：",
        "{",
        '  "is_food_label": true/false,        // 图片是否为食品标签',
        '  "label_type": "如：预包装食品标签/进口食品/营养标签豁免类 等",',
        '  "extracted": {                       // 识读到的字段，无则空字符串',
        '    "food_name":"", "ingredients":"", "additives":"", "net_content":"",',
        '    "spec":"", "producer":"", "address":"", "contact":"",',
        '    "production_date":"", "shelf_life":"", "expiry_date":"", "storage":"",',
        '    "license_no":"", "standard_code":"", "quality_grade":"",',
        '    "allergens":"", "claims":"",',
        '    "nutrition_warning":"",            // 若有“儿童青少年应避免过量摄入盐油糖”回填原文',
        '    "nutrition_table":[ {"name":"能量","value":"1234kJ","nrv":"15%"}, ... ],',
        '    "other_text":""                    // 其他重要可见文字',
        "  },",
        '  "checks": [                           // 必须覆盖清单中每个 id',
        '    {"id":"name","category":"GB7718","item":"食品名称","status":"pass",',
        '     "finding":"具体说明","basis":"GB 7718-2025 4.2"}, ...',
        "  ],",
        '  "summary": {"verdict":"compliant|issues|non_compliant|not_a_label",',
        '              "pass":0,"fail":0,"warn":0,"score":0},   // score 0-100，越高越合规',
        '  "suggestions": ["针对 fail/warn 项给出可执行的整改建议", ...]',
        "}",
        "",
        "若图片明显不是食品标签：is_food_label=false，summary.verdict=\"not_a_label\"，"
        "checks 可为空，并在 suggestions 说明原因。所有文字用简体中文。",
    ]
    return "\n".join(lines)
