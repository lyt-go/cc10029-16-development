"""交付范围区间运算：解析、合并、求差与子集判断。

交付凭据用“起止段”描述一次交付覆盖的记录范围（例如 ``1-100``、``1-40,61-100``）。
本模块只做纯区间计算，不触碰任何业务状态，供凭据服务、传输批次和前端领域层共用。
"""
from __future__ import annotations

import re

Segment = tuple[int, int]

_SEPARATOR = re.compile(r"[,，;；、\s]+")


def normalize(segments: list[Segment]) -> list[Segment]:
    """把区间列表排序并合并重叠或相邻的段。"""
    ordered = sorted((min(a, b), max(a, b)) for a, b in segments if min(a, b) >= 1)
    merged: list[Segment] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def parse_scope(text: str | None) -> list[Segment]:
    """把 ``1-100`` / ``1-40,61-100`` 这类文本解析为规范化区间列表。

    空文本返回空列表；非法或小于 1 的区间直接报错，避免把错误的交付范围静默放行。
    """
    if not text:
        return []
    raw = str(text).strip()
    if not raw:
        return []
    segments: list[Segment] = []
    for part in _SEPARATOR.split(raw):
        if not part:
            continue
        token = part.replace("~", "-").replace("—", "-").replace("–", "-")
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(token)
        if start < 1 or end < 1:
            raise ValueError(f"交付范围必须从 1 开始：{part}")
        segments.append((min(start, end), max(start, end)))
    return normalize(segments)


def format_scope(segments: list[Segment]) -> str:
    """把区间列表格式化回 ``1-40,61-100``；单条记录输出为 ``7``。"""
    parts: list[str] = []
    for start, end in normalize(segments):
        parts.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(parts)


def scope_size(segments: list[Segment]) -> int:
    """统计区间覆盖的记录条数。"""
    return sum(end - start + 1 for start, end in normalize(segments))


def union(left: list[Segment], right: list[Segment]) -> list[Segment]:
    """求两个范围的并集。"""
    return normalize(list(left) + list(right))


def subtract(base: list[Segment], removed: list[Segment]) -> list[Segment]:
    """求 ``base - removed``，返回规范化后的剩余范围。"""
    remaining = normalize(base)
    for cut_start, cut_end in normalize(removed):
        next_remaining: list[Segment] = []
        for start, end in remaining:
            if cut_end < start or cut_start > end:
                next_remaining.append((start, end))
                continue
            if start < cut_start:
                next_remaining.append((start, cut_start - 1))
            if end > cut_end:
                next_remaining.append((cut_end + 1, end))
        remaining = next_remaining
    return normalize(remaining)


def is_subset(inner: list[Segment], outer: list[Segment]) -> bool:
    """判断 ``inner`` 是否完全包含在 ``outer`` 内。"""
    return not subtract(inner, outer)


def expand(segments: list[Segment], limit: int | None = None) -> list[int]:
    """把区间展开成升序记录序号列表，可选只取前 ``limit`` 个。"""
    numbers: list[int] = []
    for start, end in normalize(segments):
        numbers.extend(range(start, end + 1))
        if limit is not None and len(numbers) >= limit:
            return numbers[:limit]
    return numbers


def first_n(segments: list[Segment], count: int) -> list[Segment]:
    """按升序从范围里取前 ``count`` 条记录。"""
    if count <= 0:
        return []
    picked: list[Segment] = []
    remaining = count
    for start, end in normalize(segments):
        take = min(end - start + 1, remaining)
        if take > 0:
            picked.append((start, start + take - 1))
            remaining -= take
        if remaining <= 0:
            break
    return normalize(picked)
