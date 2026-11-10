#!/usr/bin/env python3

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "schema.sql"
README = ROOT / "README.md"

START = "<!-- SCHEMA_MERMAID_START -->"
END = "<!-- SCHEMA_MERMAID_END -->"


def parse_schema(sql: str):
    tables = []
    relationships = []

    pattern = re.compile(
        r"CREATE TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(sql):
        table = match.group(1)
        body = match.group(2)

        tables.append(table)

        primary_key_columns = set(
            re.findall(
                r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)"
                r"\s+[^,]*?\bPRIMARY\s+KEY\b",
                body,
                re.IGNORECASE,
            )
        )

        for fk in re.finditer(
            r"FOREIGN KEY\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
            r"\s*REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            body,
            re.IGNORECASE,
        ):
            child_column = fk.group(1)
            parent_table = fk.group(2)

            # If the FK is also the child's PK, there can be at most one
            # child record per parent; otherwise assume one-to-many.
            child_cardinality = "o|" if child_column in primary_key_columns else "o{"

            relationships.append(
                (parent_table, table, child_column, child_cardinality)
            )

    return sorted(set(tables)), relationships


def build_mermaid(tables, relationships):
    lines = ["```mermaid", "erDiagram"]

    for table in tables:
        lines.append(f"    {table}")

    lines.append("")

    for parent, child, column, cardinality in relationships:
        lines.append(
            f"    {parent} ||--{cardinality} {child} : {column}"
        )

    lines.append("```")
    return "\n".join(lines)


def update_readme(mermaid: str):
    text = README.read_text()

    if START not in text or END not in text:
        raise RuntimeError(
            "README schema markers not found. "
            "Add SCHEMA_MERMAID_START and SCHEMA_MERMAID_END first."
        )

    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)

    updated = (
        before
        + START
        + "\n"
        + mermaid
        + "\n"
        + END
        + after
    )

    README.write_text(updated)


def main():
    sql = SCHEMA.read_text()
    tables, relationships = parse_schema(sql)
    mermaid = build_mermaid(tables, relationships)
    update_readme(mermaid)

    print(
        f"Updated README schema diagram: "
        f"{len(tables)} tables, {len(relationships)} relationships"
    )


if __name__ == "__main__":
    main()
