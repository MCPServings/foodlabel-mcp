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


def _checklist_text() -> str:
    return "\n".join(
        f"- [{c['id']}] {c['item']}（{c['basis']}）：{c['requirement']}" for c in CHECKLIST
    )


def eval_system() -> str:
    """DeepSeek-R1 评价各 OCR 结果质量、并合并出最佳标签文本的系统提示词。"""
    return "\n".join(
        [
            "你是中文 OCR 质量评审与文本融合专家。用户会给出**同一张预包装食品标签图片**"
            "由多个 OCR 模型分别识别出的文本。",
            "请完成两件事：",
            "1) 逐个评价每份 OCR 结果的质量（完整性、准确性、是否有乱码/幻觉/缺行），"
            "给 0-100 的可信度分数与简要说明；",
            "2) 综合所有结果，去除明显乱码与幻觉，融合出一份**最完整、最可信**的标签文本"
            "（保留原文用词、数字、单位、标点，按版面顺序分行）。",
            "不要臆造任何 OCR 结果中都没有出现的内容。所有文字用简体中文。",
            "",
            "只输出一个 JSON 对象：",
            "{",
            '  "evaluations": [',
            '    {"model":"模型名","score":0-100,"comment":"质量评价","issues":["发现的问题",...]}, ...',
            "  ],",
            '  "merged_text": "融合后的最佳标签全文（含换行）",',
            '  "confidence": 0-100   // 对融合文本整体可信度的判断',
            "}",
        ]
    )


def analyze_system() -> str:
    """把标签原图+OCR 草稿逐条对照 GB 国标、输出问题/风险/缺失点的系统提示词。"""
    return "\n".join(
        [
            "你是中国食品标签合规审查专家。用户会给出一件预包装食品标签的**原始照片**，"
            "以及 OCR 初步识别出的**文本草稿**（可能有错漏）。",
            f"请对照现行国家标准逐条详尽比对：{STANDARDS}。",
            "",
            "要求：",
            "0) **以原图为准**：OCR 草稿仅供参考，若与图片不符或有遗漏，以你从图片中看到的为准。",
            "1) 先从标签中提取结构化字段（见 extracted）；识别不到的留空，**不要臆造**。",
            "2) 对下面每一条强制检查项给出判定，并**引用具体标准条款**说明依据：",
            "   status 取值：pass=满足；fail=明确违反或缺失强制项；warn=表述不规范/疑似问题需复核；"
            "na=对本商品不适用（如进口食品豁免许可证/标准代号、属营养标签豁免类别）；"
            "unknown=文本信息不足无法判断。",
            "3) 在 problems / risks / missing 三类里给出**详尽**的问题点：",
            "   - missing（缺失点）：强制标示内容缺失（如缺生产日期、缺某营养素、缺致敏物提示等）；",
            "   - problems（问题点）：标示了但不规范/不符合条款要求（如日期格式、声称用语、定量标示等）；",
            "   - risks（风险点）：可能引发监管处罚或消费者误导的隐患（如夸大宣传、暗示功效、误导性图文等）。",
            "   每条都要尽量指出对应标准条款与整改建议。",
            "",
            "营养标签豁免参考：" + NUTRITION_EXEMPTIONS,
            "",
            "强制检查清单（basis 为标准条款，须原样回填）：",
            _checklist_text(),
            "",
            "只输出一个 JSON 对象（不要 markdown、不要解释文字）：",
            "{",
            '  "is_food_label": true/false,',
            '  "label_type": "如：预包装食品标签/进口食品/营养标签豁免类 等",',
            '  "extracted": {',
            '    "food_name":"", "ingredients":"", "additives":"", "net_content":"",',
            '    "spec":"", "producer":"", "address":"", "contact":"",',
            '    "production_date":"", "shelf_life":"", "expiry_date":"", "storage":"",',
            '    "license_no":"", "standard_code":"", "quality_grade":"",',
            '    "allergens":"", "claims":"", "nutrition_warning":"",',
            '    "nutrition_table":[ {"name":"能量","value":"1234kJ","nrv":"15%"}, ... ],',
            '    "other_text":""',
            "  },",
            '  "checks": [',
            "    // 只列出**有问题或不适用**的检查项（status 为 fail/warn/na/unknown）；",
            "    // 满足要求(pass)的项不必列出，系统会自动补全为 pass。",
            '    {"id":"date","category":"GB7718","item":"日期标示","status":"fail",',
            '     "finding":"结合标签文本的具体说明","basis":"GB 7718-2025 4.7"}, ...',
            "  ],",
            '  "missing":  [ {"item":"缺失项","detail":"说明","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "problems": [ {"item":"问题项","detail":"说明","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "risks":    [ {"item":"风险项","detail":"说明","level":"high|medium|low","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "summary": {"verdict":"compliant|issues|non_compliant|not_a_label",',
            '              "score":0}   // score 0-100，越高越合规',
            "}",
            "",
            'checks 只需列出有问题/不适用的项（用清单里的 id），不必逐项复述。'
            '若文本明显不是食品标签：is_food_label=false，'
            'verdict="not_a_label"。所有文字用简体中文。',
        ]
    )

