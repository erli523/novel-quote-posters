"""Offline, version-bound review records. AI never calls this to approve itself."""

import base64
import copy
import io
import json
from datetime import datetime
from pathlib import Path

from PIL import Image

from .albums import ALBUM_CHECKS, album_target
from .contracts import allocation_summary, load_contract, selection_issues
from .documents import commit_documents, recover_documents
from .export import quote_fingerprint, review_fingerprint
from .images import validate_character_reference
from .quotes import check_display
from .rendering import validate_tracked_render
from .review_policy import VISUAL_CHECKS, VISUAL_POLICY_VERSION, visual_review_complete
from .source import validate_source
from .source_context import CONTEXT_POLICY_VERSION, SourceContext
from .storage import contained, digest_file, load_json, now, save_json, sha256
from .workflow import Ledger

STYLE_LABELS = {"mist_cinema": "雾感电影", "archive_paper": "旧纸档案", "dark_stage": "暗色舞台",
                "ink_whitespace": "水墨留白", "modern_editorial": "现代杂志", "warm_memory": "暖色回忆"}


def task_issue_label(issue: str, definition: dict | None, card_count: int) -> str:
    if definition is None:
        return "尚未设置明确的任务要求。"
    if issue.startswith("poster count"):
        return f"目标 {definition['target_count']} 张，现有 {card_count} 张；请补齐或明确修改目标。"
    if issue.startswith("distinct style"):
        return f"至少需要 {definition['min_distinct_styles']} 套风格，当前不足。"
    key = issue.split(":", 1)[0]
    if "character/style" in issue:
        return key + "：人物或风格不在本次范围内。"
    if "type/chapter" in issue:
        return key + "：引语类型或章节不在本次范围内。"
    if "rewrite forbidden" in issue:
        return key + "：本次任务只允许原文，请恢复原句或明确修改政策。"
    if "text length" in issue:
        return key + f"：文字长度须为 {definition['min_quote_chars']}～{definition['max_quote_chars']} 字。"
    if "allocation missing" in issue:
        return "尚未设置分项调用额度，请明确分配后再新增调用或批准成品。"
    if "allocation exceeded" in issue or "call total exceeded" in issue:
        return "已有调用超过任务额度，请核对台账并明确修订契约。"
    return "任务要求尚有待处理项，请核对配置。"

def character_label(identity: str | None, characters: dict) -> str:
    if identity not in characters:
        return "未确定"
    character = characters[identity]
    name = character["name"]
    if sum(c["name"] == name for c in characters.values()) > 1:
        aliases = [a for a in character.get("aliases", []) if isinstance(a, str) and a != name]
        return f"{name}（{'、'.join(aliases) if aliases else '人物档案 ' + identity}）"
    return name


def visual_views(run: Path, card: dict, character: dict, layout: dict) -> dict:
    """Return exact local bytes and an explicitly simulated centered-square crop."""
    image = contained(run, card["poster"])
    preview = image.with_name(image.stem + "-preview.png")
    if digest_file(preview) != layout.get("preview_sha256"):
        raise ValueError("mobile preview changed before review; render a new version")
    with Image.open(image) as picture:
        side = min(picture.size)
        left, top = (picture.width-side)//2, (picture.height-side)//2
        crop_box = [left, top, left+side, top+side]
        buffer = io.BytesIO()
        picture.crop(crop_box).save(buffer, format="PNG", icc_profile=picture.info.get("icc_profile"))
    result = {"full": image.read_bytes(), "mobile": preview.read_bytes(),
              "center_square_simulation": buffer.getvalue()}
    if character.get("reference_image"):
        result["reference"] = contained(run, character["reference_image"]).read_bytes()
    return {"bytes": result, "crop_box": crop_box}


def _data_url(raw: bytes) -> str:
    with Image.open(io.BytesIO(raw)) as picture:
        mime = Image.MIME[picture.format]
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")


def review_targets(run: Path, quotes_only: bool = False) -> dict:
    recover_documents(run)
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = load_json(run / "quotes.json")
    characters = load_json(run / "characters.json")
    contract = load_contract(run, book)
    contract_hash = contract["fingerprint"] if contract else None
    current_cards = load_json(run / "cards.json") if (run / "cards.json").exists() else []
    issues = selection_issues(contract, current_cards, {q["quote_id"]: q for q in quotes}) if contract else ["explicit task contract missing"]
    with Ledger(run / ".state/ledger.sqlite") as ledger:
        issues.extend(allocation_summary(contract, ledger.budget())["allocation_issues"])
    rows = []
    contexts = SourceContext(book)
    for quote in quotes:
        check_display(book, quote, allow_rewrite=True)
        context = quote["source"]
        speaker = quote["speaker"]
        source_context = contexts.target(quote)
        rows.append({"quote_id": quote["quote_id"], "fingerprint": quote_fingerprint(quote, characters.get(speaker["character_id"])),
                     "quote_exact": quote["quote_exact"], "display_text": quote["display_text"],
                     "text_mode": quote["text_mode"], "quote_type": quote["quote_type"],
                     "speaker": character_label(speaker["character_id"], characters),
                     "speaker_status": speaker["status"], "speaker_evidence": speaker.get("evidence", []),
                     "chapter": context["chapter"],
                     "context": book["text"][context["context_start"]:context["context_end"]],
                     "source_context": source_context, "context_policy_version": CONTEXT_POLICY_VERSION,
                     "context_fingerprint": source_context["fingerprint"],
                     "status": "pending", "note": ""})
    card_rows = []
    if not quotes_only:
        by_id = {q["quote_id"]: q for q in quotes}
        for card in current_cards:
            image = contained(run, card["poster"])
            layout_path = image.with_suffix(".layout.json")
            layout = load_json(layout_path)
            if layout["output_sha256"] != digest_file(image):
                raise ValueError("poster changed before review")
            validate_tracked_render(run, card, by_id[card["quote_id"]], book,
                                    characters[card["character_id"]], layout, required=True, source_checked=True)
            character = characters[card["character_id"]]
            reference = validate_character_reference(run, character)
            views = visual_views(run, card, character, layout)
            card_rows.append({"card_id": card["card_id"], "quote_id": card["quote_id"],
                              "fingerprint": review_fingerprint(card, by_id[card["quote_id"]],
                                                               digest_file(image), digest_file(layout_path), contract_hash),
                              "poster": card["poster"], "image_sha256": digest_file(image),
                              "draft": layout["draft"], "image_kind": card.get("image_kind", "unknown"),
                              "character": character_label(card["character_id"], characters),
                              "character_traits": character.get("traits", []),
                              "reference_usable": bool(reference and reference["source_kind"] in {"generated", "user_provided"}),
                              "view_hashes": {key: sha256(raw) for key, raw in views["bytes"].items()},
                              "crop_box": views["crop_box"],
                              "visual_policy_version": VISUAL_POLICY_VERSION,
                              "visual_checks": {key: "pending" for key in VISUAL_CHECKS},
                              "status": "pending", "note": ""})
    return {"schema_version": 3, "source_hash": book["source_hash"], "book_title": book["title"],
            "reviewer_type": "human", "reviewer": "", "attested": False,
            "reviewed_at": "", "quotes": rows, "cards": card_rows, "album": album_target(book, card_rows, contract),
            "task_contract": contract, "task_issues": issues}


def write_review_page(run: Path, output: Path, *, quotes_only: bool = False):
    if output.exists():
        raise ValueError("review page exists; choose a new version")
    targets = review_targets(run, quotes_only)
    view = json.loads(json.dumps(targets, ensure_ascii=False))
    characters = load_json(run / "characters.json")
    definition = targets["task_contract"]["definition"] if targets["task_contract"] else None
    count = len(load_json(run / "cards.json")) if (run / "cards.json").exists() else 0
    view["task_summary"] = {"characters": [character_label(k, characters) for k in definition["character_ids"]],
                            "styles": [STYLE_LABELS[k] for k in definition["style_ids"]],
                            "chapters": [c["title"] for c in load_json(run / "book.json")["chapters"]
                                         if c["chapter_id"] in definition["chapter_ids"]] if definition["chapter_ids"] else ["全部章节"],
                            "quote_types": [{"dialogue": "对白", "monologue": "独白"}[k] for k in definition["quote_types"]]} if definition else None
    view["task_issue_labels"] = [task_issue_label(issue, definition, count) for issue in targets["task_issues"]]
    book = load_json(run / "book.json")
    validate_source(run, book)
    if (book["source_hash"] != targets["source_hash"] or book["title"] != targets["book_title"] or any(
            row["source_context"]["canonical_hash"] != book["canonical_hash"] for row in targets["quotes"])):
        raise ValueError("source changed while preparing full-chapter review; regenerate page")
    chapter_ids = {p["chapter_id"] for row in targets["quotes"] for p in row["source_context"]["passages"]}
    view["source_chapters"] = {c["chapter_id"]: {"title": c["title"], "start": c["start"], "end": c["end"],
                                "text": book["text"][c["start"]:c["end"]]}
                               for c in book["chapters"] if c["chapter_id"] in chapter_ids}
    cards = {card["card_id"]: card for card in load_json(run / "cards.json")} if not quotes_only else {}
    for card in view["cards"]:
        source = cards[card["card_id"]]
        layout = load_json(contained(run, source["poster"]).with_suffix(".layout.json"))
        views = visual_views(run, source, characters[source["character_id"]], layout)
        if {key: sha256(raw) for key, raw in views["bytes"].items()} != card["view_hashes"]:
            raise ValueError("review images changed while preparing page")
        card["view_data"] = {key: _data_url(raw) for key, raw in views["bytes"].items()}
    view["visual_check_labels"] = VISUAL_CHECKS
    view["album_check_labels"] = ALBUM_CHECKS
    # Prevent novel content from ending the fixed script element or inserting HTML.
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    html = PAGE.replace("__REVIEW_DATA__", data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8", newline="\n")
    save_json(output.with_suffix(".targets.json"), targets)
    return targets


def apply_review(run: Path, record: dict) -> dict:
    recover_documents(run)
    if record.get("schema_version") != 3:
        raise ValueError("review schema 3 required; regenerate old review forms without upgrading approvals")
    if (record.get("reviewer_type") != "human"
            or record.get("attested") is not True or not isinstance(record.get("reviewer"), str)
            or not record["reviewer"].strip() or not isinstance(record.get("reviewed_at"), str)):
        raise ValueError("review requires an explicit named human attestation")
    try:
        reviewed_at = datetime.fromisoformat(record["reviewed_at"])
        if reviewed_at.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise ValueError("review time must be ISO 8601 with a timezone") from error
    snapshot = {name: load_json(run / name) if (run / name).exists() else None for name in
                ["book.json", "quotes.json", "characters.json", "cards.json", "album-review.json", "task-contract.json"]}
    expected = review_targets(run, quotes_only=not bool(record.get("cards")))
    if record["source_hash"] != expected["source_hash"]:
        raise ValueError("review targets a different source")
    changes = {"quotes": {}, "cards": {}}
    for category, identity in [("quotes", "quote_id"), ("cards", "card_id")]:
        available = {row[identity]: row for row in expected[category]}
        seen = set()
        for row in record.get(category, []):
            key = row[identity]
            if key in seen or key not in available:
                raise ValueError("duplicate or unknown review target")
            seen.add(key)
            if row["fingerprint"] != available[key]["fingerprint"]:
                raise ValueError("review invalidated by a content change; regenerate review page")
            if row["status"] not in {"pending", "approved", "rejected"}:
                raise ValueError("invalid review decision")
            if row["status"] == "pending":
                continue
            if (category == "quotes" and row["status"] == "approved" and any(
                    row.get(k) != available[key][k] for k in ["source_context", "context_policy_version", "context_fingerprint"])):
                raise ValueError("source context changed or missing; regenerate attribution review page")
            if category == "cards" and row["status"] == "approved" and available[key]["draft"]:
                raise ValueError("approve a clean candidate, not a draft-labelled poster")
            if category == "cards":
                if row["status"] == "approved" and expected["task_issues"]:
                    raise ValueError("task contract blocks visual approval: " + "; ".join(expected["task_issues"]))
                if (row.get("view_hashes") != available[key]["view_hashes"]
                        or row.get("crop_box") != available[key]["crop_box"]
                        or row.get("visual_policy_version") != VISUAL_POLICY_VERSION):
                    raise ValueError("visual review views or policy changed; regenerate page")
                if row["status"] == "approved":
                    if not visual_review_complete(row):
                        raise ValueError("visual checklist must be fully passed before approval")
                    if available[key]["image_kind"] == "generated" and not available[key]["reference_usable"]:
                        raise ValueError("generated character needs a registered non-test baseline reference for visual comparison")
            changes[category][key] = {"status": row["status"], "fingerprint": row["fingerprint"],
                                      "reviewer_type": "human", "reviewer": record["reviewer"],
                                      "reviewed_at": record["reviewed_at"], "note": row.get("note", "")}
            if category == "cards":
                changes[category][key].update(visual_policy_version=VISUAL_POLICY_VERSION,
                    visual_checks=row.get("visual_checks", {}), view_hashes=row["view_hashes"], crop_box=row["crop_box"])
            elif row["status"] == "approved":
                changes[category][key].update(context_policy_version=CONTEXT_POLICY_VERSION,
                    context_fingerprint=available[key]["context_fingerprint"])
    values = {}
    album_changed = False
    album = record.get("album")
    if album is not None:
        if not isinstance(album, dict) or album.get("status") not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid whole-album review decision")
        if album["status"] != "pending":
            target = expected["album"]
            if target is None or album.get("fingerprint") != target["fingerprint"] or album.get("material") != target["material"]:
                raise ValueError("whole-album review invalidated by order/content changes")
            checks = album.get("checks", {})
            if not isinstance(checks, dict) or set(checks) != set(ALBUM_CHECKS) or any(v not in {"pending", "passed", "failed"} for v in checks.values()):
                raise ValueError("invalid whole-album checklist")
            if album["status"] == "approved":
                if expected["task_issues"]:
                    raise ValueError("task contract blocks album approval: " + "; ".join(expected["task_issues"]))
                if any(row["draft"] for row in expected["cards"]):
                    raise ValueError("approve a clean whole-album candidate, not draft-labelled images")
                if checks != dict.fromkeys(ALBUM_CHECKS, "passed"):
                    raise ValueError("whole-album checklist must be fully passed before approval")
            proposed = {"schema_version": 1, "material": target["material"], "fingerprint": target["fingerprint"],
                        "status": album["status"], "checks": checks, "reviewer_type": "human",
                        "reviewer": record["reviewer"], "attested": True, "reviewed_at": record["reviewed_at"],
                        "note": album.get("note", "")}
            if proposed != snapshot["album-review.json"]:
                values["album-review.json"] = proposed
                album_changed = True
    # Validate the whole record before committing any decisions, including the album.
    for category, identity in [("quotes", "quote_id"), ("cards", "card_id")]:
        if changes[category]:
            items = copy.deepcopy(snapshot[f"{category}.json"])
            for item in items:
                if item[identity] in changes[category]:
                    proposed = changes[category][item[identity]]
                    if item.get("review") == proposed:
                        del changes[category][item[identity]]
                    else:
                        item["review"] = proposed
            if changes[category]:
                values[f"{category}.json"] = items
    record_hash = sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode())
    history = run / "reviews" / f"{record_hash}.json"
    with Ledger(run / ".state/ledger.sqlite") as ledger, ledger.db:
        ledger.db.execute("UPDATE config SET value=value WHERE key='limit'")
        approving = any(row.get("status") == "approved" for row in record.get("cards", [])) or bool(
            record.get("album") and record["album"].get("status") == "approved")
        if not history.exists():
            save_json(history, {"record": record, "imported_at": now()})
    def check_budget(ledger):
        if approving and (issues := allocation_summary(load_contract(run), ledger.budget())["allocation_issues"]):
            raise ValueError("task contract blocks approval: " + "; ".join(issues))

    transaction = commit_documents(run, values, reason="human_review", expected=snapshot, guard=check_budget) if values else None
    return {"record_hash": record_hash, "transaction": transaction,
            "quotes_changed": len(changes["quotes"]), "cards_changed": len(changes["cards"]),
            "album_changed": album_changed}


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小说海报审核</title><style>
body{margin:0;background:#f3f1eb;color:#202d36;font:16px/1.7 system-ui,sans-serif}
main{max-width:1060px;margin:auto;padding:24px}h1{font-size:30px}h2{margin-top:36px}
article{background:white;padding:24px;margin:20px 0;border:1px solid #d6d9d5;border-radius:12px}
blockquote{font-size:24px;white-space:pre-wrap;border-left:4px solid #a47e42;padding-left:18px}
pre{white-space:pre-wrap;background:#f2f5f4;padding:16px;overflow-wrap:anywhere}
label{display:block;margin:12px 0}input,textarea,select,button{font:inherit;padding:8px}
input[type=text],textarea{width:min(95%,720px)}button{background:#183d46;color:white;border:0;border-radius:6px;cursor:pointer}
img{max-width:100%;max-height:850px;object-fit:contain;background:#eee}small{color:#52616b}
.comparison{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px}
figure{margin:0}figcaption{font-weight:600;margin:8px 0}.mobile-view{width:360px}
.visual-checks{border:1px solid #d6d9d5;padding:16px;margin-top:20px}.visual-checks label{font-size:15px}
.album-strip{display:flex;gap:16px;overflow-x:auto;padding:12px 0}.album-strip figure{flex:0 0 130px}.album-strip img{width:120px;height:160px}
dialog{max-width:90vw;max-height:90vh;overflow:auto;border:1px solid #64747c;border-radius:8px}
dialog::backdrop{background:#12212ccc}#original-view img{max-width:none;max-height:none;width:auto;height:auto;display:block;margin-top:12px}
#close-source{position:sticky;top:0;z-index:1}
.notice{background:#fff2d6;padding:16px;border-radius:8px}.sticky{position:sticky;bottom:0;background:#f3f1eb;padding:16px;border-top:1px solid #ccc}
</style><main><h1 id="title"></h1>
<p class="notice">本页只在本地展示原文和图片，不联网，不会自动批准。请逐项核对说话人、原句、人物一致性、文字与手机可读性。图片点击后可查看原尺寸。测试底图不能作为真实生图验收。</p>
<label>审核人 <input type="text" id="reviewer" placeholder="填写实际审核人"></label>
<h2>本次任务要求</h2><section id="task"></section>
<h2>原句与说话人</h2><section id="quotes"></section>
<h2>图片与排版</h2><section id="cards"></section>
<h2>图集顺序与节奏</h2><section id="album"></section>
<div class="sticky"><label><input id="attest" type="checkbox"> 我已亲自核对所作出的审核决定</label>
<button id="download">下载审核记录</button><p id="message" role="status"></p></div></main>
<dialog id="image-dialog" aria-label="原尺寸图片"><button id="close-dialog">关闭原图</button><div id="original-view"></div></dialog>
<dialog id="source-dialog" aria-label="完整章节"><button id="close-source">关闭章节</button><h2 id="source-title"></h2><p>以下为本地规范正文整章；仅供核对上下文，不代表全部都是台词。</p><pre id="source-text"></pre></dialog>
<script>"use strict";const review=__REVIEW_DATA__;
document.getElementById('title').textContent='《'+review.book_title+'》审核';
function element(tag,text,parent){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(parent)parent.append(el);return el;}
if(review.task_contract){const d=review.task_contract.definition,a=element('article',undefined,document.getElementById('task'));
 element('p','目标 '+d.target_count+' 张 · '+d.output.width+'×'+d.output.height+' · '+(d.allow_rewrite?'允许标记改写':'只用原文'),a);
 element('p','人物范围：'+review.task_summary.characters.join('、')+'；风格：'+review.task_summary.styles.join('、')+'；至少 '+d.min_distinct_styles+' 套',a);
 element('p','章节：'+review.task_summary.chapters.join('、')+'；引语：'+review.task_summary.quote_types.join('、')+'；长度 '+d.min_quote_chars+'～'+d.max_quote_chars+' 字',a);
 element('p','主题：'+(d.theme||'未限定')+'；禁用元素：'+(d.forbidden_elements.join('、')||'未另列'),a);
 element('p','主题、禁用元素与人物含义请本人逐项核对，程序不自动判断。',a);
}else{element('p','尚未设置明确任务契约，不能批准正式成品。',document.getElementById('task'));}
for(const issue of review.task_issue_labels)element('p','待处理：'+issue,document.getElementById('task'));
function openOriginal(source,description){const view=document.getElementById('original-view');view.replaceChildren();
 const image=element('img',undefined,view);image.src=source;image.alt=description;document.getElementById('image-dialog').showModal();}
document.getElementById('close-dialog').onclick=()=>document.getElementById('image-dialog').close();
function openChapter(id){const chapter=review.source_chapters[id];document.getElementById('source-title').textContent=chapter.title;
 document.getElementById('source-text').textContent=chapter.text;const dialog=document.getElementById('source-dialog');dialog.showModal();dialog.scrollTop=0;}
document.getElementById('close-source').onclick=()=>document.getElementById('source-dialog').close();
function decision(row,article){const label=element('label','审核决定 ',article);const select=element('select',undefined,label);
 for(const [value,text] of [['pending','待审核'],['approved','通过'],['rejected','退回']]){const option=element('option',text,select);option.value=value;}
 select.onchange=()=>row.status=select.value;
 const noteLabel=element('label','说明 ',article), note=element('textarea',undefined,noteLabel);note.rows=2;note.oninput=()=>row.note=note.value;}
const typeNames={dialogue:'对白',monologue:'独白',narration:'旁白或说明',unclassified:'待分类'};
const statusNames={explicit:'明确归属',context_inferred:'依据上下文',ambiguous:'归属待定',narration:'叙述文字'};
for(const row of review.quotes){const a=element('article',undefined,document.getElementById('quotes'));
 element('small',row.chapter+' · '+(typeNames[row.quote_type]||'待分类'),a);
 element('blockquote',row.display_text,a);element('p','拟归属：'+row.speaker+' · '+(statusNames[row.speaker_status]||'待核对')+' · '+(row.text_mode==='rewrite'?'用户改写':'原文片段'),a);
 if(row.text_mode==='rewrite')element('pre','原始依据：'+row.quote_exact,a);
 const details=element('details',undefined,a);element('summary','查看完整段落与归属证据',details);
 element('p','下列为原句及证据所在完整段落，并各保留相邻段。片段按原文顺序显示，省略处另列；这些上下文不是一条拼接引语。',details);
 const source=row.source_context;let previous=null;
 for(const passage of source.passages){if(previous!==null&&passage.start>previous)element('p','……此处省略 '+(passage.start-previous)+' 字，可查看整章……',details);
  element('small',passage.chapter+' · 段落 '+passage.paragraph_ids.join('、')+' · 正文位置 ['+passage.start+','+passage.end+')',details);
  element('pre',passage.text,details);const button=element('button','查看整章：'+passage.chapter,details);button.type='button';button.onclick=()=>openChapter(passage.chapter_id);previous=passage.end;}
 element('p','AI 归属说明（待本人核对）：'+(source.speaker_explanation||'未提供'),details);
 for(const item of row.speaker_evidence)element('pre','证据 ['+item.start+','+item.end+')：'+item.text,details);
 element('small','记录编号：'+row.quote_id,details);decision(row,a);}
for(const row of review.cards){const a=element('article',undefined,document.getElementById('cards'));
 element('h3',row.character+' · '+row.card_id,a);
 const origins={generated:'工具生成',user_provided:'用户提供',test_fixture:'测试底图',unknown:'来源未登记'};
 element('p','来源：'+(origins[row.image_kind]||'来源未登记')+' · '+(row.draft?'带待审核标签':'无标签候选，尚未批准'),a);
 const traits=element('details',undefined,a);element('summary','查看人物档案与依据',traits);
 const kinds={explicit:'原文明示',inferred:'推断',user_specified:'用户指定',design_choice:'设计选择'};
 for(const trait of row.character_traits){element('p',(kinds[trait.kind]||trait.kind)+'：'+trait.value,traits);for(const evidence of trait.evidence||[])element('pre',evidence.text,traits);}
 const grid=element('div',undefined,a);grid.className='comparison';
 const labels={reference:'人物基准图',full:'完整海报（点击查看原尺寸）',mobile:'手机预览 360×480',center_square_simulation:'居中正方形裁切模拟（非平台规则）'};
 if(!row.reference_usable)element('p','尚无已登记的非测试人物基准图；工具生成的成品须先补基准图再批准。',a);
 for(const key of ['reference','full','mobile','center_square_simulation']){if(!row.view_data[key])continue;
  const figure=element('figure',undefined,grid);element('figcaption',labels[key],figure);
  const image=element('img',undefined,figure);image.src=row.view_data[key];image.alt=row.character+' '+labels[key];
  if(key==='mobile')image.className='mobile-view';image.onclick=()=>openOriginal(image.src,image.alt);}
 const checks=element('fieldset',undefined,a);checks.className='visual-checks';element('legend','逐项视觉检查',checks);
 for(const [key,text] of Object.entries(review.visual_check_labels)){const label=element('label',text+' ',checks),select=element('select',undefined,label);
  for(const [value,name] of [['pending','未检查'],['passed','通过'],['failed','需修改']]){const option=element('option',name,select);option.value=value;}
  select.onchange=()=>row.visual_checks[key]=select.value;}
 decision(row,a);}
if(review.album){const a=element('article',undefined,document.getElementById('album'));
 element('h3','整组 '+review.album.material.count+' 张 · 按下列顺序上传',a);
 element('p','首张卡片兼首图，没有额外封面；请实际查看首、中、末页，再逐项判断。',a);
 const strip=element('div',undefined,a);strip.className='album-strip';
 for(const item of review.album.material.ordered_cards){const card=review.cards.find(c=>c.card_id===item.card_id),figure=element('figure',undefined,strip);
  element('figcaption',String(item.sequence).padStart(3,'0')+' · '+item.card_id,figure);
  const image=element('img',undefined,figure);image.src=card.view_data.mobile;image.alt='图集第 '+item.sequence+' 张 '+card.character;
  image.onclick=()=>openOriginal(card.view_data.full,image.alt);}
 const checks=element('fieldset',undefined,a);checks.className='visual-checks';element('legend','整组检查',checks);
 for(const [key,text] of Object.entries(review.album_check_labels)){const label=element('label',text+' ',checks),select=element('select',undefined,label);
  for(const [value,name] of [['pending','未检查'],['passed','通过'],['failed','需修改']]){const option=element('option',name,select);option.value=value;}
  select.onchange=()=>review.album.checks[key]=select.value;}
 decision(review.album,a);
}else{element('p','当前只审核原句，图片就绪后再生成图集审核页。',document.getElementById('album'));}
document.getElementById('download').onclick=()=>{const name=document.getElementById('reviewer').value.trim();
 if(!name||!document.getElementById('attest').checked){document.getElementById('message').textContent='请填写审核人，并勾选本人核对。';return;}
 if(review.task_issues.length&&(review.cards.some(c=>c.status==='approved')||(review.album&&review.album.status==='approved'))){document.getElementById('message').textContent='本次任务仍有配置或范围问题，不能批准成品；可保留待审或退回意见。';return;}
 for(const card of review.cards){if(card.status==='approved'&&(card.draft||Object.values(card.visual_checks).some(v=>v!=='passed')||(card.image_kind==='generated'&&!card.reference_usable))){document.getElementById('message').textContent=card.card_id+' 尚未满足批准条件，请检查草稿标签、基准图和逐项视觉判断。';return;}}
 if(review.album&&review.album.status==='approved'&&(review.cards.some(c=>c.draft)||Object.values(review.album.checks).some(v=>v!=='passed'))){document.getElementById('message').textContent='整组尚未满足批准条件，请检查草稿标签和整组判断。';return;}
 const result=JSON.parse(JSON.stringify(review));result.reviewer=name;result.attested=true;result.reviewed_at=new Date().toISOString();
 for(const card of result.cards)delete card.view_data;delete result.visual_check_labels;delete result.album_check_labels;delete result.task_summary;delete result.task_issue_labels;delete result.source_chapters;
 const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download='review-record.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('message').textContent='审核记录已下载；导入后只对相同版本有效。';};
</script></html>'''
