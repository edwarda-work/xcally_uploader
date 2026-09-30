"""Read-only CSV segmentation previews; never creates xCALLY resources."""

import csv
import io
from collections import Counter


def preview_segments(contents: bytes, agent_column: str | None = None) -> dict:
    try:
        reader = csv.reader(io.StringIO(contents.decode("utf-8-sig"), newline=""), strict=True)
        headers = [value.strip() for value in next(reader, [])]
        named = [value for value in headers if value]
        if not named:
            raise ValueError("CSV must contain named headers.")
        if len(named) != len(set(named)):
            raise ValueError("CSV contains duplicate headers.")
        if agent_column is None:
            agent_column = next((h for h in headers if h.upper() in {"AGENT", "AGENT_NAME", "AGENT NAME"}), None)
        if agent_column is not None and agent_column not in named:
            raise ValueError("Select an agent column from this CSV.")
        column = headers.index(agent_column) if agent_column else None
        groups: Counter[str] = Counter()
        total = unassigned = 0
        for row in reader:
            if not any(value.strip() for value in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV row ending at line {reader.line_num} has {len(row)} columns; expected {len(headers)}.")
            total += 1
            if column is not None:
                agent = row[column].strip()
                if agent:
                    groups[agent] += 1
                else:
                    unassigned += 1
        if not total:
            raise ValueError("CSV contains no contacts.")
        return {"headers": named, "agent_column": agent_column, "total": total,
                "unassigned": unassigned,
                "segments": [{"agent": agent, "count": count} for agent, count in groups.items()]}
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding.") from exc
    except csv.Error as exc:
        raise ValueError("CSV could not be parsed. Check its quoting and column format.") from exc
