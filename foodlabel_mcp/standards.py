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

# 营养相关的检查项 id（属营养标签豁免时这些判 na）。
_NUTRITION_IDS = ["nutrition_table", "nutrition_warning", "trans_fat", "nutrition_value_form", "fortifier"]

# ── 固定食品类目体系：严格对应国标条款，限制 LLM 只在以下类目中二选一 ──
# 设计依据（国标原文，非自创）：
#   GB 28050-2025 §7 豁免强制标示营养标签的预包装食品（逐条列举的类别）；
#   GB 7718-2025 §10.1 豁免保质期、§10.2 豁免生产日期 的食品清单；
#   GB 7718-2025 §8 进口食品（豁免 license/std_code，正交维度，见 applicable_for 的 scope）。
# 每个类目用 `exempt` 确定性标注该类别下国标豁免的检查项 id（规则映射，不靠 LLM）。
# 国标里"按食品类别区分标示要求"仅限以下豁免，无更细的层级化大分类，故类目即对应这些条款。
FOOD_CATEGORIES: list[dict] = [
    {
        "id": "general", "name": "一般预包装食品", "basis": "GB 7718-2025 / GB 28050-2025 通则",
        "desc": "不属于任何豁免情形的常规定量包装食品（饼干、糕点、乳制品、肉制品、饮料、罐头、速食、复合调味料、酱油、酱腌菜、腐乳等）。",
        "exempt": [],
    },
    {
        "id": "fresh", "name": "生鲜食品和粮食籽粒", "basis": "GB 28050-2025 §7",
        "desc": "预包装、未经烹煮、未添加其他配料的生肉/生鱼/生鲜蛋/鲜豆/生蔬果，以及粮谷类籽粒。咸鸭蛋/虾丸等加调味或冷冻调理食品不属此类。",
        "exempt": _NUTRITION_IDS,
    },
    {
        "id": "dried_single", "name": "单一原料干制品", "basis": "GB 28050-2025 §7",
        "desc": "经切割/碾磨/粉碎等简单物理处理、未加其他配料、单一来源、未明显改变营养组成的干制品（谷物杂粮、干制果蔬、干制菌藻等）。",
        "exempt": _NUTRITION_IDS,
    },
    {
        "id": "water_tea", "name": "包装饮用水/茶叶", "basis": "GB 28050-2025 §7",
        "desc": "饮用天然矿泉水/纯净水/其他饮用水；茶叶（含袋泡茶、花果茶）。",
        "exempt": _NUTRITION_IDS,
    },
    {
        "id": "alcohol", "name": "饮料酒（酒精度>0.5%vol）", "basis": "GB 28050-2025 §7；GB 7718-2025 §10.1/§10.2",
        "desc": "发酵酒/蒸馏酒及其配制酒等酒精度>0.5%vol 的饮料酒。营养豁免；其中葡萄酒及酒精度≥10%vol 酒类还可豁免保质期(标生产日期前提)与生产日期(标批号前提)。",
        "exempt": _NUTRITION_IDS,
    },
    {
        "id": "small_intake", "name": "每日食用量≤10g(mL)/单一原料调味品", "basis": "GB 28050-2025 §7；GB 7718-2025 §10.1",
        "desc": "食盐/味精/食醋/食糖/淀粉糖/蜂蜜/单一原料香辛料/酵母/料酒等。其中食醋/食用盐/固态食糖/味精还可豁免保质期。不含腐乳/酱腌菜/酱油/酱类/复合调味料。",
        "exempt": _NUTRITION_IDS,
    },
    {
        "id": "small_package", "name": "小包装（最大表面积≤40cm²）", "basis": "GB 28050-2025 §7；GB 7718-2025 §10.3(≤20cm²)",
        "desc": "包装物/容器最大表面面积≤40cm² 的食品；营养标签可用文字格式并省 NRV%。若≤20cm² 则按 GB7718 §10.3 可进一步简化标示。",
        "exempt": ["nutrition_warning"],
    },
    {
        "id": "special", "name": "特殊膳食用/特殊食品", "basis": "GB 7718-2025 §6；GB 28050-2025 §8（按 GB13432）",
        "desc": "婴幼儿配方食品、特殊医学用途配方食品、保健食品等。其标签/营养标签按相应专门标准(如 GB13432)执行，本通则部分条款不直接适用。",
        "exempt": _NUTRITION_IDS,
    },
]
_CATEGORY_BY_ID = {c["id"]: c for c in FOOD_CATEGORIES}


def category_info(category_id: str) -> dict:
    """返回某固定类目的信息（name/basis/desc/exempt）；未知则回落 general。"""
    return _CATEGORY_BY_ID.get(category_id) or _CATEGORY_BY_ID["general"]


def applicable_for(category_id: str, scope: str = "domestic") -> dict[str, dict]:
    """根据固定类目 + 国内/进口范围，确定性地算出每个检查项是否适用。

    返回 {id: {"applicable": bool, "reason": str, "basis": str}}，覆盖全部 CHECKLIST id。
    这是严格按国标条款的规则映射（非 LLM 自由判断），保证适用范围稳定准确。
    """
    cat = _CATEGORY_BY_ID.get(category_id) or _CATEGORY_BY_ID["general"]
    exempt = set(cat["exempt"])
    is_import = scope == "import"
    out: dict[str, dict] = {}
    for c in CHECKLIST:
        cid = c["id"]
        applicable, reason = True, "适用"
        if cid in exempt:
            applicable, reason = False, f"{cat['name']}：依 {cat['basis']} 豁免"
        elif is_import and cid in ("license", "std_code"):
            applicable, reason = False, "进口食品：依 GB 7718-2025 §8.7 豁免食品生产许可证编号/产品标准代号"
        out[cid] = {"applicable": applicable, "reason": reason, "basis": c["basis"]}
    return out


def _categories_text() -> str:
    return "\n".join(f"- [{c['id']}] {c['name']}（依据 {c['basis']}）：{c['desc']}" for c in FOOD_CATEGORIES)


def _checklist_text() -> str:
    return "\n".join(
        f"- [{c['id']}] {c['item']}（{c['basis']}）：{c['requirement']}" for c in CHECKLIST
    )


def extract_system() -> str:
    """视觉识读：看图（+OCR 草稿）提取结构化字段与营养成分表，不做合规判断。"""
    return "\n".join(
        [
            "你是食品标签识读助手。用户会给出一件预包装食品标签的**原始照片**，"
            "可能附带 OCR 初步识别的**文本草稿**（仅供参考，可能有错漏）。",
            "请**以原图为准**，逐字识读标签上的全部信息，提取为结构化字段。",
            "只识读、补全文字，**不做合规判断、不推理、不评价**。识别不到的字段留空，**不要臆造**。",
            "所有文字用简体中文。只输出一个 JSON 对象：",
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
            "  }",
            "}",
            "若图片明显不是食品标签：is_food_label=false，extracted 各字段留空。",
        ]
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


def rules_system() -> str:
    """第一步：只做受限分类——从固定食品类目中选一个 + 判断是否进口。

    适用条目不让 LLM 自由发挥，而是由代码 applicable_for() 按固定映射确定性算出，
    以此约束输出范围、提升准确度与稳定性。
    """
    return "\n".join(
        [
            "你是中国食品标签合规审查专家。用户会给出一件预包装食品**已识读出的结构化字段**。",
            f"现行国家标准：{STANDARDS}。",
            "你的任务**只做分类**（不要做合规判定、不要列适用条款）：",
            "1) 从下面**固定的食品类目**中选出**唯一最匹配**的一个，返回其 id；"
            "若不属于任何豁免类目，选 general。",
            "2) 判断该食品是否为**进口食品**（标签有进口商/原产国/外文等）。",
            "",
            "固定食品类目（只能从中选一个 id，不得自创）：",
            _categories_text(),
            "",
            "判定提示：腐乳/酱腌菜/酱油/酱类/复合调味料属 general（不豁免营养）；"
            "加了调味或冷冻调理的不算 fresh；拿不准时优先选 general。",
            "",
            "只输出一个 JSON 对象（不要 markdown、不要解释文字）：",
            "{",
            '  "is_food_label": true/false,',
            '  "category_id": "上面类目之一的 id",',
            '  "category_reason": "选该类目的简要理由（引用食品名称/属性）",',
            '  "is_import": true/false',
            "}",
            "所有文字用简体中文。",
        ]
    )


def analyze_system() -> str:
    """第二步：基于已识读字段 + 已判定的适用规则，逐条评价缺失/问题/风险。"""
    return "\n".join(
        [
            "你是中国食品标签合规审查专家。用户会给出一件预包装食品**已识读出的结构化字段**，"
            "以及上一步已判定好的**适用规则**（哪些检查项适用、哪些豁免）。",
            f"请基于这些字段与适用规则，对照现行国家标准逐条详尽比对：{STANDARDS}。",
            "",
            "要求：",
            "1) 对下面每一条强制检查项给出判定，并**引用具体标准条款**说明依据：",
            "   status 取值：pass=满足；fail=明确违反或缺失强制项；warn=表述不规范/疑似问题需复核；"
            "na=对本商品不适用；unknown=信息不足无法判断。",
            "   **凡上一步『适用规则』中 applicable=false 的检查项，一律判为 na**，不计入缺失/问题。",
            "2) 仅对『适用』的检查项，在 problems / risks / missing 三类里给出**详尽**的问题点：",
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
            '  "checks": [',
            "    // 只列出**有问题或不适用**的检查项（status 为 fail/warn/na/unknown）；",
            "    // 满足要求(pass)的项不必列出，系统会自动补全为 pass。",
            '    {"id":"date","category":"GB7718","item":"日期标示","status":"fail",',
            '     "finding":"结合标签字段的具体说明","basis":"GB 7718-2025 4.7"}, ...',
            "  ],",
            '  "missing":  [ {"item":"缺失项","detail":"说明","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "problems": [ {"item":"问题项","detail":"说明","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "risks":    [ {"item":"风险项","detail":"说明","level":"high|medium|low","basis":"条款","suggestion":"整改建议"}, ... ],',
            '  "summary": {"verdict":"compliant|issues|non_compliant|not_a_label",',
            '              "score":0}   // score 0-100，越高越合规',
            "}",
            "",
            'checks 只需列出有问题/不适用的项（用清单里的 id），不必逐项复述。所有文字用简体中文。',
        ]
    )

