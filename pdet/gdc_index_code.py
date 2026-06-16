import subprocess
import yaml
import json
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
REPO_URL    = "https://github.com/NCI-GDC/gdcdictionary.git"
BRANCH      = "develop"
CLONE_DIR   = Path("/home/joseph_cottrell_99/ICR/JosephCottrell_2026Q2/data/epi700/gdcdictionary")
SCHEMAS_DIR = CLONE_DIR / "src/gdcdictionary/schemas"
OUTPUT_PATH = Path("/home/joseph_cottrell_99/ICR/JosephCottrell_2026Q2/data/epi700/gdc_field_index.json")
SKIP_FILES  = {"README.md", "_terms_enum.yaml"}

# ── Load _definitions.yaml ────────────────────────────────────────────────────
def load_definitions(schemas_dir: Path) -> dict:
    with open(schemas_dir / "_definitions.yaml") as f:
        raw = yaml.safe_load(f)
    definitions = {}
    for def_name, def_body in raw.items():
        if not isinstance(def_body, dict):
            continue
        definitions[def_name] = {
            "type":        def_body.get("type", "enum" if "enum" in def_body else "unknown"),
            "description": def_body.get("description", ""),
            "enum_values": def_body.get("enum", []),
        }
    return definitions

# ── Load _terms.yaml ──────────────────────────────────────────────────────────
def load_terms(schemas_dir: Path) -> dict:
    with open(schemas_dir / "_terms.yaml") as f:
        raw = yaml.safe_load(f)
    # Structure is: term_name -> { common: { description: "..." }, ... }
    terms = {}
    for term_name, term_body in raw.items():
        if not isinstance(term_body, dict):
            continue
        # Try to get description from the 'common' sub-key first, then top-level
        desc = (
            term_body.get("common", {}).get("description")
            or term_body.get("description", "")
        )
        if desc:
            terms[term_name] = desc
    return terms

# ── Resolve a single $ref string ──────────────────────────────────────────────
def resolve_ref_str(ref_str: str, definitions: dict, terms: dict) -> dict:
    result = {}

    if "_terms.yaml#/" in ref_str:
        # e.g. "_terms.yaml#/experimental_strategy/common"
        # extract the first path segment after #/
        key = ref_str.split("_terms.yaml#/")[-1].split("/")[0]
        desc = terms.get(key, "")
        if desc:
            result["description"] = desc

    elif "_definitions.yaml#/" in ref_str:
        # e.g. "_definitions.yaml#/data_type"
        key = ref_str.split("_definitions.yaml#/")[-1].split("/")[0]
        result = definitions.get(key, {})

    return result

# ── Resolve $ref (handles both string and list forms) ─────────────────────────
def resolve_ref(field_def: dict, definitions: dict, terms: dict) -> dict:
    ref_val = field_def.get("$ref")
    if ref_val is None:
        return {}

    # $ref can be a string or a list of strings
    refs = ref_val if isinstance(ref_val, list) else [ref_val]

    merged = {}
    for ref_str in refs:
        resolved = resolve_ref_str(ref_str, definitions, terms)
        # Merge: don't overwrite already-found values
        for k, v in resolved.items():
            if k not in merged or not merged[k]:
                merged[k] = v

    return merged

# ── Parse a single field ──────────────────────────────────────────────────────
def parse_field(field_def: dict, definitions: dict, terms: dict) -> dict:
    if not isinstance(field_def, dict):
        return {}

    result = {
        "type":        field_def.get("type", ""),
        "description": field_def.get("description", ""),
        "enum_values": field_def.get("enum", []),
    }

    # Resolve $ref (now handles list form and _terms.yaml refs)
    resolved = resolve_ref(field_def, definitions, terms)
    result["type"]        = result["type"]        or resolved.get("type", "")
    result["description"] = result["description"] or resolved.get("description", "")
    result["enum_values"] = result["enum_values"] or resolved.get("enum_values", [])

    # Handle oneOf / anyOf
    for union_key in ("oneOf", "anyOf"):
        if union_key in field_def and not result["type"]:
            for option in field_def[union_key]:
                if isinstance(option, dict) and option.get("type") not in (None, "null"):
                    result["type"] = option.get("type", "")
                    result["enum_values"] = option.get("enum", result["enum_values"])
                    break

    result["type"] = result["type"] or "unknown"
    return result


def build_gdc_index(clone_dir: Path = CLONE_DIR,
                    schemas_dir: Path = SCHEMAS_DIR,
                    output_path: Path = OUTPUT_PATH):
    # ── Clone / update repo ───────────────────────────────────────────────────
    if clone_dir.exists():
        print("Repo already cloned — pulling latest...")
        subprocess.run(["git", "-C", str(clone_dir), "pull"], check=True, capture_output=True)
    else:
        print("Cloning GDC dictionary repo (first run)...")
        subprocess.run([
            "git", "clone", "--depth=1", "--branch", BRANCH,
            REPO_URL, str(clone_dir)
        ], check=True, capture_output=True)

    # ── Build index ───────────────────────────────────────────────────────────
    print("Loading shared definition files...")
    definitions = load_definitions(schemas_dir)
    terms       = load_terms(schemas_dir)

    index = {}

    for yaml_file in sorted(schemas_dir.glob("*.yaml")):
        if yaml_file.name in SKIP_FILES:
            continue
        if yaml_file.name.startswith("_"):
            continue

        entity = yaml_file.stem

        with open(yaml_file) as f:
            schema = yaml.safe_load(f)

        for field_name, field_def in schema.get("properties", {}).items():
            parsed = parse_field(field_def, definitions, terms)
            if not parsed:
                continue
            gdc_path = f"{entity}.{field_name}"
            index[gdc_path] = {
                "entity": entity,
                "field":  field_name,
                **parsed
            }

    # ── Save ──────────────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(index, f, indent=2)

    entities = len(set(v["entity"] for v in index.values()))
    print(f"✓ Indexed {len(index)} fields across {entities} entities — saved to {output_path}")