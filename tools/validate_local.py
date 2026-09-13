#!/usr/bin/env python3
"""Local stand-in for the checks CI would run, for a host without Home Assistant.

Home Assistant needs `fcntl` and will not install on Windows, so nothing here
imports it. What this checks is everything that can be checked from the files
themselves - manifest sanity, translation coverage, reserved states, the
quality-scale rule list, and the redaction promises diagnostics.py makes.

It deliberately does NOT claim to prove the integration loads. That needs a
real Home Assistant and is tracked as the open gap in docs/API.md; a validator
that printed "all checks passed" while the integration could not be imported
would be worse than no validator.

    python tools/validate_local.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "base_power"

# Pinned from developers.home-assistant.io/docs/core/integration-quality-scale/checklist
# (54 rules). Pinned rather than discovered because a quality_scale.yaml that
# omits a rule reads as complete; checking against the full list turns an
# omission into a failure.
ALL_RULES = {
    "action-setup", "appropriate-polling", "brands", "common-modules",
    "config-flow-test-coverage", "config-flow", "dependency-transparency",
    "docs-actions", "docs-conditions", "docs-high-level-description",
    "docs-installation-instructions", "docs-removal-instructions",
    "docs-triggers", "entity-event-setup", "entity-unique-id",
    "has-entity-name", "runtime-data", "test-before-configure",
    "test-before-setup", "unique-config-entry",
    "action-exceptions", "config-entry-unloading",
    "docs-configuration-parameters", "docs-installation-parameters",
    "entity-unavailable", "integration-owner", "log-when-unavailable",
    "parallel-updates", "reauthentication-flow", "test-coverage",
    "devices", "diagnostics", "discovery-update-info", "discovery",
    "docs-data-update", "docs-examples", "docs-known-limitations",
    "docs-supported-devices", "docs-supported-functions",
    "docs-troubleshooting", "docs-use-cases", "dynamic-devices",
    "entity-category", "entity-device-class", "entity-disabled-by-default",
    "entity-translations", "exception-translations", "icon-translations",
    "reconfiguration-flow", "repair-issues", "stale-devices",
    "async-dependency", "inject-websession", "strict-typing",
}
VALID_STATUS = {"done", "todo", "exempt"}

# Home Assistant reserves these; an ENUM option or a translated state using one
# is indistinguishable from "no value".
RESERVED_STATES = {"unknown", "unavailable", "none", ""}

MANIFEST_REQUIRED = {"domain", "name", "codeowners", "documentation", "version"}

failures: list[str] = []
notes: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:
        failures.append(f"{path.relative_to(ROOT)} is not valid JSON: {err}")
        return None


def main() -> int:
    # ---------------------------------------------------------- manifest
    manifest = load_json(COMPONENT / "manifest.json") or {}
    missing = MANIFEST_REQUIRED - manifest.keys()
    check(not missing, f"manifest.json is missing {sorted(missing)}")
    check(
        manifest.get("domain") == COMPONENT.name,
        f"manifest domain {manifest.get('domain')!r} != directory {COMPONENT.name!r}",
    )
    # A custom integration does not get a quality-scale badge, and claiming one
    # is the kind of untrue-but-green thing this validator exists to stop.
    check(
        "quality_scale" not in manifest,
        "manifest.json must NOT declare quality_scale: that key is for core integrations",
    )
    check(
        isinstance(manifest.get("requirements"), list),
        "manifest.json should carry a requirements list, even if empty",
    )

    # ------------------------------------------------ strings/translations
    strings = load_json(COMPONENT / "strings.json") or {}
    en = load_json(COMPONENT / "translations" / "en.json") or {}
    check(strings == en, "strings.json and translations/en.json have diverged")

    # Every translation_key used in code must exist in strings.json, and every
    # entity entry in strings.json must be used. Either direction failing is a
    # user-visible name that silently falls back to the entity id.
    declared: set[str] = set()
    for domain_block in (strings.get("entity") or {}).values():
        declared.update(domain_block.keys())

    # Entity keys only: anything raised with a translation_domain is an
    # exception or a repair issue and is checked further down instead.
    used: set[str] = set()
    for py in COMPONENT.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        all_keys = set(re.findall(r'translation_key="([^"]+)"', src))
        raised_keys = set(
            re.findall(r'translation_domain=[^,]+,\s*translation_key="([a-z_]+)"', src)
        )
        used.update(all_keys - raised_keys)
    missing_strings = used - declared
    unused_strings = declared - used
    check(
        not missing_strings,
        f"translation keys used but not in strings.json: {sorted(missing_strings)}",
    )
    check(not unused_strings, f"strings.json entries nothing uses: {sorted(unused_strings)}")

    # ------------------------------------------------- reserved ENUM states
    for domain_block in (strings.get("entity") or {}).values():
        for key, entry in domain_block.items():
            for state in (entry.get("state") or {}):
                check(
                    state.lower() not in RESERVED_STATES,
                    f"{key} translates the reserved state {state!r}",
                )

    sensor_src = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    for options in re.findall(r"options=\[(.*?)\]", sensor_src, re.S):
        for value in re.findall(r'"([^"]*)"', options):
            check(
                value.lower() not in RESERVED_STATES,
                f"sensor.py lists the reserved state {value!r} in ENUM options",
            )

    # ENUM options and translated states must match exactly, or the UI shows
    # raw values for whatever is missing.
    enum_options = set()
    for options in re.findall(r"options=\[(.*?)\]", sensor_src, re.S):
        enum_options.update(re.findall(r'"([^"]*)"', options))
    if enum_options:
        translated = set(
            strings.get("entity", {}).get("sensor", {}).get("battery_state", {}).get("state") or {}
        )
        check(
            enum_options == translated,
            f"ENUM options and translated states differ: {sorted(enum_options ^ translated)}",
        )

    # ------------------------------------------------------ quality scale
    qs_path = COMPONENT / "quality_scale.yaml"
    if not qs_path.exists():
        failures.append("custom_components/base_power/quality_scale.yaml is missing")
    else:
        text = qs_path.read_text(encoding="utf-8")
        # Parsed by regex on purpose: PyYAML is not a dependency of this repo
        # and the file's shape is fixed.
        listed = dict(re.findall(r"^  ([a-z0-9-]+):\s*$\n\s+status:\s*(\w+)", text, re.M))
        listed.update(dict(re.findall(r"^  ([a-z0-9-]+):\s*\n\s+status:\s*(\w+)", text, re.M)))
        missing_rules = ALL_RULES - listed.keys()
        unknown_rules = listed.keys() - ALL_RULES
        check(
            not missing_rules,
            f"quality_scale.yaml omits {len(missing_rules)} rules: "
            f"{sorted(missing_rules)[:6]}",
        )
        check(not unknown_rules, f"quality_scale.yaml has unknown rules: {sorted(unknown_rules)}")
        bad = {k: v for k, v in listed.items() if v not in VALID_STATUS}
        check(not bad, f"quality_scale.yaml has invalid statuses: {bad}")
        done = sum(1 for v in listed.values() if v == "done")
        todo = sum(1 for v in listed.values() if v == "todo")
        notes.append(f"quality scale: {done} done, {todo} todo, {len(listed) - done - todo} exempt")

    # --------------------------------------------- exception translations
    # Three ways this drifts, all silent at runtime: a key raised but never
    # declared renders as the bare key, a key declared but never raised is
    # dead text, and a placeholder in the message that no raise site supplies
    # produces a KeyError inside Home Assistant's own formatting.
    # Scoped by CONTEXT, not by name: an exception or a repair issue is
    # raised with translation_domain alongside the key, and an entity
    # description never is. Matching on the key alone would make each check
    # see the other's keys and fail on both.
    declared_exceptions = set(strings.get("exceptions") or {})
    translated_raises: set[str] = set()
    for py in COMPONENT.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        translated_raises.update(
            re.findall(
                r"translation_domain=[^,]+,\s*translation_key=\"([a-z_]+)\"", src
            )
        )
    # The repair issue uses the same shape but lives under "issues".
    raised = translated_raises - set(strings.get("issues") or {})
    check(
        not (raised - declared_exceptions),
        f"exceptions raised but not in strings.json: {sorted(raised - declared_exceptions)}",
    )
    check(
        not (declared_exceptions - raised),
        f"strings.json declares exceptions nothing raises: {sorted(declared_exceptions - raised)}",
    )
    all_source = "\n".join(p.read_text(encoding="utf-8") for p in COMPONENT.glob("*.py"))
    for key, entry in (strings.get("exceptions") or {}).items():
        for placeholder in re.findall(r"\{(\w+)\}", entry.get("message", "")):
            check(
                f'"{placeholder}"' in all_source,
                f"exception {key} uses placeholder {placeholder!r} that no raise site supplies",
            )

    # ------------------------------------------------- diagnostics redaction
    diag = COMPONENT / "diagnostics.py"
    if diag.exists():
        src = diag.read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        # The credential must never be read into the report. Reading the
        # CONF_CLIENT_JWT key is allowed only as a boolean presence check.
        for match in re.findall(r"^.*CONF_CLIENT_JWT.*$", src, re.M):
            if "import" in match:
                continue
            check(
                "bool(" in match,
                f"diagnostics.py reads the credential outside a presence check: {match.strip()!r}",
            )
        check(
            "wifi_ssid" not in src or "wifi_ssid_present" in src,
            "diagnostics.py must not publish the Wi-Fi SSID",
        )
        check(
            "_fingerprint" in names or "address_id_fingerprint" in src,
            "diagnostics.py should publish a digest of the address id, not the id",
        )

    # ------------------------------------------------------- entity hygiene
    for platform in ("sensor.py", "binary_sensor.py"):
        src = (COMPONENT / platform).read_text(encoding="utf-8")
        check("PARALLEL_UPDATES" in src, f"{platform} does not set PARALLEL_UPDATES")
        check("_attr_unique_id" in src, f"{platform} sets no unique id")

    # ------------------------------------------------------------- report
    for note in notes:
        print(f"  NOTE   {note}")
    if failures:
        for f in failures:
            print(f"  FAIL   {f}")
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("  all offline checks passed")
    print("  NOT CHECKED: that the integration imports - that needs Home Assistant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
