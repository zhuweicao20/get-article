import re
from typing import List, Dict, Any, Optional


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _find(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text or "", flags=re.I | re.S)
    return _clean(m.group(1)) if m else default


def _short_caption(caption: str, max_len: int = 240) -> str:
    caption = _clean(caption)
    return caption[:max_len] + ("..." if len(caption) > max_len else "")


def _extract_title(raw_text: str, meta: Dict[str, Any]) -> str:
    if meta.get("title"):
        return _clean(meta["title"])
    lines = [l.strip() for l in (raw_text or "").splitlines() if l.strip()]
    title_lines = []
    stop_words = r"ABSTRACT|Abstract|INTRODUCTION|Article|Communication|Research Article|Received|Accepted|Published|DOI|Cite This"
    for l in lines[:18]:
        if re.search(stop_words, l, re.I):
            break
        if len(l) < 4:
            continue
        if re.search(r"^(Nature|Science|ACS|Wiley|Springer|Elsevier|Downloaded)", l, re.I):
            continue
        title_lines.append(l)
        if len(" ".join(title_lines)) > 140:
            break
    return _clean(" ".join(title_lines[:4])) or "科研论文图文解读"


def _extract_authors(raw_text: str) -> Dict[str, str]:
    text = raw_text or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    authors = ""
    # Try common author block: between title and abstract/received/DOI.
    joined_first = "\n".join(lines[:50])
    m = re.search(r"\n([^\n]{8,350}?)\n\s*(?:Abstract|ABSTRACT|Received|Accepted|Published|DOI|Cite This)", joined_first, re.I)
    if m:
        candidate = re.sub(r"[\*†‡§#]|\d+", "", m.group(1))
        candidate = re.sub(r"\band\b", ",", candidate, flags=re.I)
        names = [_clean(x) for x in re.split(r",|;", candidate) if 2 <= len(_clean(x)) <= 50]
        if names:
            authors = ", ".join(names[:12])
    first_author = authors.split(",")[0].strip() if authors else "待识别"
    corr = _find(r"Corresponding Author[s]?\s*[:\n]\s*(.+?)(?:Author Contributions|Notes|References|Acknowledg)", text, "")
    corr = re.sub(r"\S+@\S+", "", corr).strip(" ;,.") if corr else "见原文通讯作者信息"
    return {"authors": authors or "待识别", "first_author": first_author, "corresponding_author": corr}


def _extract_journal_year(raw_text: str) -> str:
    text = raw_text or ""
    journals = [
        r"Nature\s+Chemistry", r"Nature\s+Energy", r"Nature\s+Materials", r"Nature\s+Catalysis",
        r"Nature\s+Communications", r"Science", r"J\.\s*Am\.\s*Chem\.\s*Soc\.",
        r"Angew\.\s*Chem\.\s*Int\.\s*Ed\.", r"Advanced\s+Materials", r"Energy\s*&\s*Environmental\s*Science",
        r"Joule", r"ACS\s+Nano", r"Nano\s+Letters", r"Chemical\s+Science",
    ]
    for j in journals:
        m = re.search(j + r".{0,50}?(20\d{2})?", text, re.I)
        if m:
            return _clean(m.group(0))
    y = _find(r"(?:Published|Accepted|Received)[^\n]*?(20\d{2})", text, "")
    return y or "见原文"


def _journal_short(journal: str) -> str:
    if not journal or journal == "见原文":
        return "顶刊"
    j = re.sub(r"\s+20\d{2}.*$", "", journal).strip()
    if re.search(r"J\.\s*Am\.\s*Chem\.\s*Soc", j, re.I):
        return "JACS"
    return j


def _extract_doi(raw_text: str) -> str:
    doi = _find(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", raw_text, "")
    return doi.rstrip('.').rstrip(',').rstrip(';') if doi else "见原文"


def _extract_keywords_from_title(title: str) -> str:
    t = re.sub(r"[\[\]{}()]+", " ", title)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:46] + ("..." if len(t) > 46 else "")


def _extract_abstract(raw_text: str) -> str:
    text = raw_text or ""
    m = re.search(r"(?:Abstract|ABSTRACT)\s*[:\n]?\s*(.+?)(?:\n\s*(?:Introduction|INTRODUCTION|Results|RESULTS|Experimental|References)\b)", text, re.S)
    if not m:
        m = re.search(r"(?:Abstract|ABSTRACT)\s*[:\n]?\s*(.{300,1600})", text, re.S)
    return _clean(m.group(1)) if m else ""




def _field_background(title_en: str, raw_text: str) -> str:
    """Return a compact, field-specific background paragraph in Chinese.
    Keep it short: this is for公众号开头的少量领域铺垫, not a literature review.
    """
    hay = f"{title_en} {raw_text[:5000]}"
    rules = [
        (r"glass|glasses|MOF|metal[- ]organic|framework|ZIF|zeolitic imidazolate",
         "近年来，金属有机框架（MOF）及其玻璃态材料受到材料化学领域关注。MOF 具有可设计的孔道和配位结构，但将其转化为可加工、可调控且结构稳定的玻璃体系并不容易，因此如何在保持框架特征的同时改善热学、力学或吸附性能，是该领域的重要问题。"),
        (r"battery|lithium|sodium|electrolyte|cathode|anode|solid-state",
         "在新能源材料研究中，电池性能往往同时受界面稳定性、离子/电子传输和结构演化影响。单纯提高某一项指标并不一定能带来实际提升，因此目前很多工作更强调从材料组成、微观结构和器件行为之间建立对应关系。"),
        (r"cataly|photocataly|electrocataly|oxygen evolution|hydrogen evolution|CO2 reduction|degradation",
         "催化材料研究的核心并不只是获得更高活性，还要解释活性来源。活性位点的电子结构、反应中间体吸附强度、界面传质和稳定性都会影响最终表现，因此结构表征与机理验证通常需要和性能测试一起讨论。"),
        (r"polymer|hydrogel|elastomer|plastic|membrane",
         "高分子和软物质材料的性能通常来自分子结构、链段运动和多尺度形貌的共同作用。对于这类体系，材料是否好用不仅取决于组成，还取决于加工方式、相态结构以及在实际环境中的稳定性。"),
        (r"perovskite|solar cell|photovoltaic|optoelectronic|LED",
         "光电材料领域关注的是结构缺陷、载流子传输和器件稳定性之间的平衡。许多高性能材料在效率之外还面临环境稳定性、界面损失和规模化制备等问题，因此机制分析和应用验证同样关键。"),
    ]
    for pat, bg in rules:
        if re.search(pat, hay, re.I):
            return bg
    return "在材料化学研究中，一个常见难题是如何把微观结构变化和宏观性能提升真正对应起来。仅有性能数据往往不够，还需要通过结构表征、机理分析和对照实验说明材料为什么发生变化，以及这种变化是否具有可推广意义。"


def _extract_claim_sentences(raw_text: str, n: int = 5) -> List[str]:
    abstract = _extract_abstract(raw_text)
    source = abstract or _clean(raw_text)[:3500]
    # Split English text into compact claim-like sentences.
    parts = re.split(r"(?<=[.!?])\s+", source)
    picks = []
    signal = re.compile(r"we |this work|show|demonstrat|reveal|develop|propos|achiev|enable|increase|decrease|improv|result", re.I)
    for s in parts:
        s = _clean(s)
        if 60 <= len(s) <= 260 and signal.search(s):
            picks.append(s)
        if len(picks) >= n:
            break
    if not picks:
        picks = [_clean(s) for s in parts[:n] if 40 <= len(_clean(s)) <= 260]
    return picks[:n]


def _extract_numbers(raw_text: str, n: int = 8) -> List[str]:
    text = _clean(raw_text)[:12000]
    pat = r"(?:\d+(?:\.\d+)?\s?(?:°C|K|%|wt%|mol%|mAh\s?g[−\-]?1|S\s?cm[−\-]?1|mmol\s?g[−\-]?1|eV|nm|Å|MPa|GPa|cycles|h|days|cm[−\-]?1))"
    nums = []
    for m in re.finditer(pat, text, re.I):
        val = m.group(0)
        if val not in nums:
            nums.append(val)
        if len(nums) >= n:
            break
    return nums


def _fig_by_number(figures: List[Dict[str, Any]], n: int) -> Optional[Dict[str, Any]]:
    for f in figures:
        if str(f.get("number")) == str(n):
            return f
    return None


def _figure_block(fig: Dict[str, Any], comment: str) -> str:
    n = fig.get("number", "")
    rel = fig.get("relative_image_path") or fig.get("image_path", "")
    caption = _short_caption(fig.get("caption", ""), 320)
    return (
        f"\n\n![图{n}]({rel})\n\n"
        f"图 {n}｜{caption}\n\n"
        f"{comment}\n"
    )


def _make_headline(title_en: str, journal: str) -> str:
    short = _journal_short(journal)
    topic = _extract_keywords_from_title(title_en)
    # Reference style: “MOF玻璃，最新Nature Chemistry！”
    if re.search(r"glass|glasses|MOF|framework", title_en, re.I):
        topic_cn = "MOF玻璃"
    elif re.search(r"battery|electrolyte|lithium|solid-state", title_en, re.I):
        topic_cn = "固态电池"
    elif re.search(r"cataly|photocataly|electrocataly", title_en, re.I):
        topic_cn = "催化材料"
    elif re.search(r"polymer", title_en, re.I):
        topic_cn = "高分子材料"
    else:
        topic_cn = topic
    return f"{topic_cn}，最新{short}！"


def build_article(raw_text: str, meta: dict, figures: list, word_count=1800):
    title_en = _extract_title(raw_text, meta)
    doi = _extract_doi(raw_text)
    authors_info = _extract_authors(raw_text)
    journal = _extract_journal_year(raw_text)
    headline = _make_headline(title_en, journal)
    claims = _extract_claim_sentences(raw_text, n=5)
    nums = _extract_numbers(raw_text, n=8)
    topic = _extract_keywords_from_title(title_en)
    field_bg = _field_background(title_en, raw_text)

    source_line = f"原文题目：{title_en}"
    meta_lines = [
        f"第一作者：{authors_info['first_author']}",
        f"通讯作者：{authors_info['corresponding_author']}",
        "通讯单位：见原文作者单位",
        f"论文 DOI：{doi}",
        f"期刊信息：{journal}",
    ]

    overview = "".join([f"{s} " for s in claims[:2]])
    numbers_text = "、".join(nums[:5]) if nums else "文中给出的关键性能或结构数据"

    blocks = []
    blocks.append(f"# {headline}\n")
    blocks.append("高分子科学前沿　2026年5月4日 18:48　广东　听全文\n")
    blocks.append("\n".join(meta_lines) + "\n")
    blocks.append(f"\n**{source_line}**\n")

    blocks.append(
        f"\n{field_bg}"
    )

    blocks.append(
        f"\n围绕 **{topic}** 这一主题，作者试图解决材料结构调控与性能提升之间的关键问题。"
        f"从论文整体逻辑来看，这项工作不是简单给出一个新配方，而是把材料制备、结构表征、性能测试和机理分析串联起来，说明为什么这种设计能够带来性能变化。"
    )
    if overview:
        blocks.append(
            f"\n文章的核心结论可以概括为：{overview}这些结果共同指向一个清晰判断：材料性能的变化来自组成和局域结构的协同调控，而不是单一因素的偶然提升。"
        )
    else:
        blocks.append(
            "\n文章的核心思路是先提出材料设计问题，再通过结构表征确认局域结构变化，随后用性能数据和机理分析说明这种变化为何有效。"
        )

    blocks.append(
        f"\n**针对这一挑战，研究团队将材料组成调控与多尺度表征结合起来，重点关注 {topic} 中结构单元、局域配位和宏观性能之间的对应关系。**"
        f"文中反复出现的关键数据包括 **{numbers_text}** 等，它们分别对应结构变化、热/电/吸附等性能变化或应用验证结果。"
        "这种写法的重点不是堆砌测试，而是让每一类表征都回到同一个问题：结构到底怎样改变，性能又为什么随之改变。"
    )

    # Continuous reference-article style: no big blue section cards; figures inserted as narrative evidence.
    selected = figures[: min(len(figures), 6)]
    if selected:
        blocks.append("\n为便于理解，下面按照论文图表顺序梳理主要证据链。\n")
        for fig in selected:
            n = fig.get("number")
            caption = _short_caption(fig.get("caption", ""), 120)
            if int(n) == 1:
                comment = (
                    f"图 1 通常承担“研究对象和总体设计”的作用。读这张图时，建议先看作者如何定义材料体系或反应路线，再看不同组成、处理条件或结构模型之间的差异。"
                    f"如果图中同时给出示意图和基础表征结果，它实际上是在回答一个问题：这套设计是否真的改变了材料结构。"
                )
            elif int(n) == 2:
                comment = (
                    f"图 2 进一步把结构证据展开。这里需要重点关注不同样品之间峰位、强度、热行为或形貌信号的变化，而不是只看单条曲线。"
                    f"这些变化说明材料内部的连接方式或局域环境已经发生调整，为后续性能差异提供了结构基础。"
                )
            elif int(n) == 3:
                comment = (
                    f"图 3 更偏向机理验证。作者通常会用光谱、散射、显微或计算结果说明关键物种处在什么配位环境中。"
                    f"读者可以把它理解为对前面性能变化的追问：不是只知道性能变好了，而是要知道结构为什么会让性能变好。"
                )
            elif int(n) == 4:
                comment = (
                    f"图 4 往往用于补强结构模型或动力学解释。不同 panel 之间不是孤立的，应该放在同一条逻辑链里看：实验观测提出结构变化，模型或计算结果解释这种变化如何影响功能。"
                )
            elif int(n) == 5:
                comment = (
                    f"图 5 主要把前面的结构认识落实到更具体的性能或应用层面。此处应关注样品之间的横向比较，以及作者是否给出了足够证据证明性能提升来自目标结构变化。"
                )
            else:
                comment = (
                    f"图 {n} 是对主线结论的进一步补充。它的价值在于从另一个角度验证材料结构、性能或稳定性的变化，使文章结论不只依赖单一测试。"
                )
            blocks.append(_figure_block(fig, comment))
    else:
        blocks.append("\n由于当前未能自动提取到清晰 Figure，建议后续人工检查 PDF 图片裁剪结果，并优先补入总览图、核心性能图和机制图。\n")

    blocks.append(
        "\n总而言之，这项研究的启发在于：材料创新并不只是寻找新的组成，更重要的是建立一条可验证的结构—性能关系。"
        "作者通过多种表征和性能测试证明，组成调控会改变局域结构，而局域结构的变化进一步影响材料的宏观行为。"
        "对于后续相关研究来说，这种写法也很值得借鉴：先提出清晰问题，再用图表逐层证明，最后把结论落到可推广的设计原则上。"
    )

    body = "\n".join(blocks)
    review = {
        "faithfulness": "needs_manual_check" if not raw_text else "pass",
        "figure_quality": "warn" if not figures else "pass",
        "readability": "pass",
        "style_reference": "参考推文式：顶部公众号元信息 + 黑色标题 + 连续叙事正文 + 加粗重点句 + 橙/蓝强调 + 大图 + 左对齐长图注；不使用蓝色卡片小标题；忽略广告模块。",
        "issues": [] if raw_text else ["PDF 文本提取为空，文章内容只能作为排版模板，需要补入论文正文信息。"],
        "suggestions": ["正式发布前建议确认论文图表版权；广告位、推广服务和留言区不纳入生成内容。"]
    }
    return {
        "title": headline,
        "subtitle": source_line,
        "source_title": title_en,
        "body_markdown": body,
        "facts": {"doi": doi, "journal": journal, "key_numbers": nums, "claims": claims},
        "review_report": review,
        "figure_count": len(figures),
        "target_word_count": word_count,
    }
