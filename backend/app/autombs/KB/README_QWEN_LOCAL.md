# MBS KB Builder — Qwen Local (Ollama)

This version runs **locally** using **Qwen / Qwen3-8B Instruct** via **Ollama** + LangChain.

## Prereqs
1. Install **Ollama**: https://ollama.com
2. Pull a Qwen model (choose one your machine can handle):
   ```bash
   ollama pull qwen3:8b
   # or
   ollama pull qwen2.5:7b-instruct
   ```
3. Confirm it runs:
   ```bash
   ollama run qwen3:8b
   ```

## Install deps (Python)
```bash
pip install -r requirements_qwen_local.txt
```

## Run (dry run 2 records)
```bash
python kb_builder_qwen_local.py   --xml MBS-XML-20250701_Version_3.XML   --norm mbs_normalization_pack_v1.json   --schema mbs_item_schema_v2_enforced.json   --out mbs_kb.jsonl   --ollama-model qwen3:8b   --limit 2 --dry
```

## Run full build
```bash
python kb_builder_qwen_local.py   --xml MBS-XML-20250701_Version_3.XML   --norm mbs_normalization_pack_v1.json   --schema mbs_item_schema_v2_enforced.json   --out mbs_kb.jsonl   --ollama-model qwen3:8b
```

### Notes
- The script validates JSON against the **enum-enforced schema** and runs a repair pass when needed.
- Each output line is **one JSON record**. Use `jq` to pretty-print.
