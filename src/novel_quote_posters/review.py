"""Offline, version-bound review records. AI never calls this to approve itself."""

import base64
import json
from datetime import datetime
from pathlib import Path

from .documents import commit_documents, recover_documents
from .export import quote_fingerprint, review_fingerprint
from .quotes import check_display
from .rendering import validate_tracked_render
from .source import validate_source
from .storage import contained, digest_file, load_json, now, save_json, sha256


def character_label(identity: str | None, characters: dict) -> str:
    if identity not in characters:
        return "未确定"
    character = characters[identity]
    name = character["name"]
    if sum(c["name"] == name for c in characters.values()) > 1:
        aliases = [a for a in character.get("aliases", []) if isinstance(a, str) and a != name]
        return f"{name}（{'、'.join(aliases) if aliases else '人物档案 ' + identity}）"
    return name


def review_targets(run: Path, quotes_only: bool = False) -> dict:
    recover_documents(run)
    book = load_json(run / "book.json")
    validate_source(run, book)
    quotes = load_json(run / "quotes.json")
    characters = load_json(run / "characters.json")
    rows = []
    for quote in quotes:
        check_display(book, quote, allow_rewrite=True)
        context = quote["source"]
        speaker = quote["speaker"]
        rows.append({"quote_id": quote["quote_id"], "fingerprint": quote_fingerprint(quote, characters.get(speaker["character_id"])),
                     "quote_exact": quote["quote_exact"], "display_text": quote["display_text"],
                     "text_mode": quote["text_mode"], "quote_type": quote["quote_type"],
                     "speaker": character_label(speaker["character_id"], characters),
                     "speaker_status": speaker["status"], "speaker_evidence": speaker.get("evidence", []),
                     "chapter": context["chapter"],
                     "context": book["text"][context["context_start"]:context["context_end"]],
                     "status": "pending", "note": ""})
    card_rows = []
    if not quotes_only:
        by_id = {q["quote_id"]: q for q in quotes}
        for card in load_json(run / "cards.json"):
            image = contained(run, card["poster"])
            layout_path = image.with_suffix(".layout.json")
            layout = load_json(layout_path)
            if layout["output_sha256"] != digest_file(image):
                raise ValueError("poster changed before review")
            validate_tracked_render(run, card, by_id[card["quote_id"]], book,
                                    characters[card["character_id"]], layout, source_checked=True)
            card_rows.append({"card_id": card["card_id"], "quote_id": card["quote_id"],
                              "fingerprint": review_fingerprint(card, by_id[card["quote_id"]],
                                                               digest_file(image), digest_file(layout_path)),
                              "poster": card["poster"], "image_sha256": digest_file(image),
                              "draft": layout["draft"], "image_kind": card.get("image_kind", "unknown"),
                              "status": "pending", "note": ""})
    return {"schema_version": 1, "source_hash": book["source_hash"], "book_title": book["title"],
            "reviewer_type": "human", "reviewer": "", "attested": False,
            "reviewed_at": "", "quotes": rows, "cards": card_rows}


def write_review_page(run: Path, output: Path, *, quotes_only: bool = False):
    if output.exists():
        raise ValueError("review page exists; choose a new version")
    targets = review_targets(run, quotes_only)
    view = json.loads(json.dumps(targets, ensure_ascii=False))
    for card in view["cards"]:
        raw = contained(run, card["poster"]).read_bytes()
        card["image_data"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    # Prevent novel content from ending the fixed script element or inserting HTML.
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    html = PAGE.replace("__REVIEW_DATA__", data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8", newline="\n")
    save_json(output.with_suffix(".targets.json"), targets)
    return targets


def apply_review(run: Path, record: dict) -> dict:
    recover_documents(run)
    if (record.get("schema_version") != 1 or record.get("reviewer_type") != "human"
            or record.get("attested") is not True or not isinstance(record.get("reviewer"), str)
            or not record["reviewer"].strip() or not isinstance(record.get("reviewed_at"), str)):
        raise ValueError("review requires an explicit named human attestation")
    try:
        reviewed_at = datetime.fromisoformat(record["reviewed_at"])
        if reviewed_at.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise ValueError("review time must be ISO 8601 with a timezone") from error
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
            if category == "cards" and row["status"] == "approved" and available[key]["draft"]:
                raise ValueError("approve a clean candidate, not a draft-labelled poster")
            changes[category][key] = {"status": row["status"], "fingerprint": row["fingerprint"],
                                      "reviewer_type": "human", "reviewer": record["reviewer"],
                                      "reviewed_at": record["reviewed_at"], "note": row.get("note", "")}
    # Validate the whole record before committing any decisions.
    values = {}
    for category, identity in [("quotes", "quote_id"), ("cards", "card_id")]:
        if changes[category]:
            items = load_json(run / f"{category}.json")
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
    if not history.exists():
        save_json(history, {"record": record, "imported_at": now()})
    transaction = commit_documents(run, values, reason="human_review") if values else None
    return {"record_hash": record_hash, "transaction": transaction,
            "quotes_changed": len(changes["quotes"]), "cards_changed": len(changes["cards"])}


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
.notice{background:#fff2d6;padding:16px;border-radius:8px}.sticky{position:sticky;bottom:0;background:#f3f1eb;padding:16px;border-top:1px solid #ccc}
</style><main><h1 id="title"></h1>
<p class="notice">本页只在本地展示原文和图片，不联网，不会自动批准。请逐项核对说话人、原句、人物一致性、文字与手机可读性。图片点击后可查看原尺寸。测试底图不能作为真实生图验收。</p>
<label>审核人 <input type="text" id="reviewer" placeholder="填写实际审核人"></label>
<h2>原句与说话人</h2><section id="quotes"></section>
<h2>图片与排版</h2><section id="cards"></section>
<div class="sticky"><label><input id="attest" type="checkbox"> 我已亲自核对所作出的审核决定</label>
<button id="download">下载审核记录</button><p id="message" role="status"></p></div></main>
<script>"use strict";const review=__REVIEW_DATA__;
document.getElementById('title').textContent='《'+review.book_title+'》审核';
function element(tag,text,parent){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(parent)parent.append(el);return el;}
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
 const details=element('details',undefined,a);element('summary','查看上下文与归属证据',details);element('pre',row.context,details);
 element('pre',row.speaker_evidence.map(e=>e.text).join(String.fromCharCode(10)),details);element('small','记录编号：'+row.quote_id,details);decision(row,a);}
for(const row of review.cards){const a=element('article',undefined,document.getElementById('cards'));
 element('h3',row.card_id,a);element('p','来源：'+row.image_kind+' · '+(row.draft?'带待审核标签':'无标签候选，尚未批准'),a);
 const image=element('img',undefined,a);image.src=row.image_data;image.alt=row.card_id+' 海报预览';
 image.onclick=()=>{image.style.maxHeight=image.style.maxHeight==='none'?'850px':'none';};decision(row,a);}
document.getElementById('download').onclick=()=>{const name=document.getElementById('reviewer').value.trim();
 if(!name||!document.getElementById('attest').checked){document.getElementById('message').textContent='请填写审核人，并勾选本人核对。';return;}
 const result=JSON.parse(JSON.stringify(review));result.reviewer=name;result.attested=true;result.reviewed_at=new Date().toISOString();
 for(const card of result.cards)delete card.image_data;
 const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download='review-record.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('message').textContent='审核记录已下载；导入后只对相同版本有效。';};
</script></html>'''
