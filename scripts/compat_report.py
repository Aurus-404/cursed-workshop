"""Group compat_sweep.jsonl results by cause; optionally write a Markdown summary.

    python scripts/compat_report.py outputs/compat_sweep.jsonl [docs/COMPATIBILIDAD.md]
"""
import collections
import json
import re
import sys
from datetime import date

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
ok = sum(row["ok"] for row in rows)
print("ok", ok, "/", len(rows))
groups, examples = collections.Counter(), {}
for row in rows:
    if row["ok"]:
        continue
    key = row["stage"] + ": " + re.sub(r"'[^']*'|\b[\w./-]*(/|_)[\w./-]*\b|\d+", "…", row["error"])[:110]
    groups[key] += 1
    examples.setdefault(key, []).append((row["hull"], row["donor"], row["error"][:180]))
for key, count in groups.most_common():
    print(count, key)
    for example in examples[key][:2]:
        print("     ", example)

if len(sys.argv) > 2:
    by_nation = collections.defaultdict(lambda: [0, 0])
    by_family = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        for nation, family in (row["hull_group"], row["donor_group"]):
            by_nation[nation.lstrip("+")][0] += row["ok"]; by_nation[nation.lstrip("+")][1] += 1
            by_family[family][0] += row["ok"]; by_family[family][1] += 1

    def table(title, data):
        lines = [f"### {title}", "", "| | Combinaciones | Construidas |", "|---|---:|---:|"]
        for name, (good, total) in sorted(data.items(), key=lambda item: -item[1][1]):
            lines.append(f"| {name} | {total} | {round(100*good/total)}% |")
        return "\n".join(lines)

    failures = "\n".join(f"- {count}× `{key}`" for key, count in groups.most_common()) or "- Ninguna."
    text = f"""# Compatibilidad

Barrido automático del {date.today().isoformat()}: {len(rows)} combinaciones al azar de chasis y módulos
(juego base, DLC y MACE; todas las naciones y familias), compiladas y registradas con la herramienta.
**{ok} de {len(rows)} ({round(100*ok/len(rows))}%) se construyen.** Cada vehículo cuenta como chasis y como módulo.

Esto comprueba que la herramienta arma y registra el vehículo; el comportamiento dentro del juego se
verifica aparte, en partidas reales.

{table("Por nación", by_nation)}

{table("Por familia", by_family)}

### Motivos de las que no se construyen

{failures}
"""
    open(sys.argv[2], "w", encoding="utf-8").write(text)
    print("written", sys.argv[2])
