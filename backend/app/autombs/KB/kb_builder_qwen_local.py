#!/usr/bin/env python3
import os, sys, re, json, time, argparse
from typing import Dict, Any, List, Optional
from xml.etree.ElementTree import iterparse

from jsonschema import Draft202012Validator

# LangChain + Ollama (local)
from langchain_community.chat_models import ChatOllama
from langchain.schema import SystemMessage, HumanMessage

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_date(d: str) -> Optional[str]:
    if not d: return None
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})$", d.strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None

def xml_records(xml_path: str, group_filter: Optional[str], item_allow: Optional[set]):
    for event, elem in iterparse(xml_path, events=("end",)):
        if elem.tag != "Data": continue
        rec = {}
        for tag in ["ItemNum","ItemStartDate","ItemEndDate","Category","Group","SubGroup","SubHeading",
                    "ItemType","FeeType","ProviderType","ScheduleFee","BenefitType","Benefit75","Benefit85",
                    "Benefit100","BenefitStartDate","EMSNCap","EMSNFixedCapAmount","EMSNMaximumCap",
                    "EMSNPercentageCap","EMSNDescription","DerivedFeeStartDate","DerivedFee",
                    "DescriptionStartDate","Description"]:
            text = elem.findtext(tag)
            rec[tag] = text.strip() if text else ""
        elem.clear()

        if group_filter and rec["Group"] != group_filter:
            continue
        if item_allow and rec["ItemNum"] not in item_allow:
            continue

        yield rec

def minimal_scaffold(rec: Dict[str,str]) -> Dict[str, Any]:
    return {
        "$schema": "mbs.item.v2",
        "item_number": rec["ItemNum"],
        "title": None,
        "group": rec["Group"] or None,
        "effective_from": parse_date(rec["ItemStartDate"]),
        "effective_to": parse_date(rec["ItemEndDate"]),
        "hard_gates": {},
        "soft_gates": None,
        "pricing_benefit": {},
        "frequency_caps": [],
        "prohibitions": None,
        "authority_refs": [{
            "source": "MBS XML 1 Jul 2025",
            "anchor": rec["ItemNum"],
            "quote": ""
        }],
        "display": {
            "description_original": rec["Description"] or None,
            "derived_fee_original": rec["DerivedFee"] or None
        },
        "meta": {
            "category": rec["Category"] or None,
            "subgroup": rec["SubGroup"] or None,
            "subheading": rec["SubHeading"] or None,
            "item_type": rec["ItemType"] or None,
            "fee_type": rec["FeeType"] or None,
            "provider_type_code": rec["ProviderType"] or None,
            "benefit_start": parse_date(rec["BenefitStartDate"]),
            "description_version_from": parse_date(rec["DescriptionStartDate"]),
            "benefit_amounts": {
                "benefit75": float(rec["Benefit75"]) if rec["Benefit75"] else None,
                "benefit85": float(rec["Benefit85"]) if rec["Benefit85"] else None,
                "benefit100": float(rec["Benefit100"]) if rec["Benefit100"] else None
            }
        }
    }

SYSTEM_INSTRUCTIONS = """You are a meticulous Medicare Benefits Schedule (MBS) coding analyst.
Read ONE MBS XML record and output ONLY a JSON object that matches the provided JSON Schema.
- Use ONLY the canonical enums provided for provider roles, settings, modes, referral types, benefit basis, EMSN cap.
- If the record does not state a field, set it to null or an empty list.
- Keep the full descriptor verbatim in display.description_original.
- Put one short decisive phrase from the descriptor into authority_refs[0].quote to justify the main gates.
- Do not explain. Return ONLY the JSON object.
"""

def build_user_prompt(rec: Dict[str,str], scaffold: Dict[str,Any], norm: Dict[str,Any], schema: Dict[str,Any]) -> str:
    enums = {
        "provider_roles_allowed": norm["provider_roles"]["enum"],
        "locations_allowed": norm["setting_vocab"]["enum"],
        "hospital_episode": ["admitted","not_admitted","either"],
        "modes_allowed": norm["mode_vocab"]["enum"],
        "referrer_types": norm["referral_vocab"]["enum"],
        "benefit_basis_default": norm["benefit_basis_enum"],
        "emsn_cap_code": norm["emsn_cap_code_enum"],
        "association_bans_scope": norm["association_bans"]["scope_enum"],
        "frequency_scopes": norm["frequency_caps"]["scope_enum"]
    }

    mapping = {
        "BenefitType_map": {
            "A": "75_percent",
            "B": "85_percent",
            "C": "75_or_85_percent",
            "D": "75_or_100_percent",
            "E": "100_percent"
        },
        "FeeType_rule": "If FeeType='D', schedule_fee=null and copy DerivedFee into pricing_benefit.derived_fee.text_original and display.derived_fee_original."
    }

    xml_block = "\n".join([f"{k}: {rec.get(k,'')}" for k in [
        "ItemNum","ItemStartDate","ItemEndDate","Category","Group","SubGroup","SubHeading",
        "ItemType","FeeType","ProviderType","ScheduleFee","BenefitType","Benefit75","Benefit85",
        "Benefit100","BenefitStartDate","EMSNCap","EMSNFixedCapAmount","EMSNMaximumCap",
        "EMSNPercentageCap","EMSNDescription","DerivedFeeStartDate","DerivedFee",
        "DescriptionStartDate","Description"
    ]])

    schema_json = json.dumps(schema, ensure_ascii=False)
    scaffold_json = json.dumps(scaffold, ensure_ascii=False, indent=2)
    enums_json = json.dumps(enums, ensure_ascii=False, indent=2)
    mapping_json = json.dumps(mapping, ensure_ascii=False, indent=2)

    prompt = f"""MBS XML RECORD (verbatim)
{xml_block}

SCAFFOLD (prefilled fields; keep descriptor verbatim)
{scaffold_json}

CANONICAL ENUMS (use ONLY these tokens for normalized fields)
{enums_json}

MAPPING RULES
{mapping_json}

JSON SCHEMA (obey exactly)
{schema_json}

Return ONLY the JSON object for this item. No commentary.
"""
    return prompt

def validate_obj(obj: Dict[str,Any], schema: Dict[str,Any]) -> List[str]:
    v = Draft202012Validator(schema)
    return [f"{'/'.join([str(x) for x in e.path])}: {e.message}" for e in v.iter_errors(obj)]

def extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).rstrip("`").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end+1]
    return t

def build_kb(args):
    norm = load_json(args.norm)
    schema = load_json(args.schema)
    model = ChatOllama(model=args.ollama_model, temperature=0)

    total = good = failed = 0

    with open(args.out, "w", encoding="utf-8") as out:
        allow_items = set(args.items.split(",")) if args.items else None
        for rec in xml_records(args.xml, args.group, allow_items):
            total += 1
            scaffold = minimal_scaffold(rec)
            user_prompt = build_user_prompt(rec, scaffold, norm, schema)

            if args.dry:
                print("----- SYSTEM -----")
                print(SYSTEM_INSTRUCTIONS)
                print("----- USER -----")
                print(user_prompt)
                if args.limit and total >= args.limit: break
                continue

            try:
                resp = model.invoke([SystemMessage(content=SYSTEM_INSTRUCTIONS),
                                     HumanMessage(content=user_prompt)])
                raw = extract_json(resp.content)
                obj = json.loads(raw)
                errors = validate_obj(obj, schema)
                if errors:
                    repair_prompt = user_prompt + "\\nVALIDATION_ERRORS:\\n" + "\\n".join(errors) + "\\nFix JSON to satisfy the schema exactly. Return ONLY JSON."
                    resp2 = model.invoke([SystemMessage(content=SYSTEM_INSTRUCTIONS + " Be extremely strict about JSON keys and enums."),
                                          HumanMessage(content=repair_prompt)])
                    raw2 = extract_json(resp2.content)
                    obj = json.loads(raw2)
                    errors = validate_obj(obj, schema)

                if errors:
                    raise ValueError("Validation failed: " + "; ".join(errors))

                out.write(json.dumps(obj, ensure_ascii=False) + "\n")
                good += 1
            except Exception as e:
                failed += 1
                sys.stderr.write(f"[FAIL] Item {rec['ItemNum']}: {e}\\n")

            if args.limit and total >= args.limit: break

    print(json.dumps({"processed": total, "ok": good, "failed": failed, "out": args.out}, indent=2))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--norm", required=True, help="Normalization pack JSON path")
    ap.add_argument("--schema", required=True, help="Enum-enforced schema JSON path")
    ap.add_argument("--out", required=True, help="Output JSONL")
    ap.add_argument("--ollama-model", default="qwen3:8b", help="Local Ollama model name")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--group", default=None, help="Optional Group filter, e.g., A21")
    ap.add_argument("--items", default=None, help="Comma-separated specific ItemNum list")
    args = ap.parse_args()
    build_kb(args)

if __name__ == "__main__":
    main()
