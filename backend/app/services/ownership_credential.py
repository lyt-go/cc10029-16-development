"""批次数据归属凭据领域服务：把一次交付“归谁、交什么范围、已领用到哪”固化成可核对的凭据。

背景
----
源端与目标端之间原先只有批次号和条数，缺少一份能说明“这份批次数据归属于哪个源端、
覆盖哪些记录范围、目标端实际领用了多少”的凭据。数据域交接（例如订单域从源端 A
交给源端 B）时，同一目标端会同时收到两个来源的记录，仅凭批次号无法判断某条落地记录
该记在 A 还是 B 名下，也无法判断旧凭据是否还能继续发运。

本模块提供四类能力：
1. 配置快照校验：从源端登记、目标端登记、抽取窗口与批次读取配置快照，核对凭据声明的
   交付范围是否落在快照允许的记录范围内。
2. 领用范围区分：凭据的交付范围拆成「已领用范围」和「未领用范围」，只有已领用部分
   才允许发起传输。
3. 归属核对：按目标端 + 记录范围反查每条记录归属的源端与凭据，交接期两个来源同时
   存在时能给出重叠冲突。
4. 交接留痕：交接只追加记录，不重写已落地/已结案的旧批；交接策略决定旧凭据是
   「保留到结案」还是「发货前重新确认」，且不会替新接收方撤销旧凭据。
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services import scope_range
from app.services.base import BaseModuleService
from app.store import store

POLICY_RETAIN = "保留到结案"
POLICY_RECONFIRM = "发货前重新确认"
HANDOVER_POLICIES = (POLICY_RETAIN, POLICY_RECONFIRM)

# 已落地、已结案的凭据属于历史事实，不允许被交接或撤销改写。
IMMUTABLE_STAGES = ("已落地", "已结案")


class OwnershipCredentialService(BaseModuleService):
    MODULE = "ownership_credential"
    ENTITY = "批次归属凭据"
    OPERATION_TERM = "核对"
    LIST_FIELDS = [
        "凭据编号", "源端编号", "目标编号", "数据域", "批次编号", "交付范围",
        "已领用范围", "未领用范围", "起始序号", "凭据阶段", "交接策略",
        "配置快照", "回执编号",
    ]
    REQUIRED_FIELDS = ["凭据编号", "源端编号", "目标编号", "交付范围"]
    STATUS_ORDER = ["草稿", "待领用", "已领用", "待重新确认", "已落地", "已结案", "已撤销"]
    ACTION_RULES = {
        "提交确认": "待领用",
        "登记领用": "已领用",
        "确认落地": "已落地",
        "确认结案": "已结案",
        "撤销凭据": "已撤销",
        "要求重新确认": "待重新确认",
        "确认发运": "已领用",
    }
    NEGATIVE_ACTIONS = ["撤销凭据"]

    # -- 配置快照与范围校验 --

    def build_config_snapshot(
        self, source_code: str, target_code: str, batch_code: str = "", window_code: str = ""
    ) -> dict[str, Any]:
        """从源端/目标端登记、抽取窗口和传输批次组装配置快照。

        快照只取与“这份数据能不能发”有关的配置项：源端的记录格式、编码、抽取方式，
        目标端的接收格式、落地方式和批量上限，以及窗口/批次声明的记录条数。
        找不到的配置项置空，不让缺配置的凭据悄悄通过校验。
        """
        source = next(
            (r for r in store.rows("source_register") if r.get("源端编号") == source_code), None)
        target = next(
            (r for r in store.rows("target_register") if r.get("目标编号") == target_code), None)

        window = None
        if window_code:
            window = next(
                (r for r in store.rows("extract_window") if r.get("窗口编号") == window_code), None)
        if window is None and batch_code:
            batch_row = next(
                (r for r in store.rows("transfer_batch") if r.get("批次编号") == batch_code), None)
            if batch_row and batch_row.get("窗口编号"):
                window = next(
                    (r for r in store.rows("extract_window")
                     if r.get("窗口编号") == batch_row.get("窗口编号")), None)
        if window is None:
            window = next(
                (r for r in store.rows("extract_window") if r.get("源端编号") == source_code), None)

        batch = next(
            (r for r in store.rows("transfer_batch") if r.get("批次编号") == batch_code), None)

        def _as_count(value: Any) -> int | None:
            try:
                number = int(str(value).strip())
            except (TypeError, ValueError):
                return None
            return number if number >= 0 else None

        available = None
        if batch is not None:
            available = _as_count(batch.get("记录条数"))
        if available is None and window is not None:
            available = _as_count(window.get("抽取条数"))

        snapshot = {
            "源端编号": source_code,
            "源端已登记": bool(source and source.get("status") == "已登记"),
            "源端记录格式": (source or {}).get("记录格式", ""),
            "源端编码方式": (source or {}).get("编码方式", ""),
            "源端抽取方式": (source or {}).get("抽取方式", ""),
            "数据域": (source or {}).get("数据域", ""),
            "目标编号": target_code,
            "目标已登记": bool(target and target.get("status") == "已登记"),
            "目标接收格式": (target or {}).get("接收格式", ""),
            "目标落地方式": (target or {}).get("落地方式", ""),
            "批量上限": (target or {}).get("批量上限", ""),
            "窗口编号": (window or {}).get("窗口编号", ""),
            "窗口起点": (window or {}).get("窗口起点", ""),
            "窗口终点": (window or {}).get("窗口终点", ""),
            "批次编号": batch_code,
            "批次记录条数": (batch or {}).get("记录条数", ""),
            "可交付记录数": available,
            "快照时间": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time())),
        }
        digest = self._compute_checksum(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        snapshot["快照摘要"] = digest
        return snapshot

    def _snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        cached = entry.get("_config_snapshot")
        if isinstance(cached, dict):
            return cached
        return self.build_config_snapshot(
            str(entry.get("源端编号", "")),
            str(entry.get("目标编号", "")),
            str(entry.get("批次编号", "")),
            str(entry.get("窗口编号", "")),
        )

    def verify_scope(self, cred_id: int) -> dict[str, Any]:
        """按配置快照核对凭据交付范围，回答“目标端实际能接收哪一份源数据”。

        返回逐项结论而不是一个笼统的布尔值：快照是否仍与登记配置一致、交付范围是否
        超出可交付记录数、已领用/未领用是否守恒、声明来源是否仍登记有效。
        """
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        return self._verify_scope_entry(entry)

    def _verify_scope_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        scope = scope_range.parse_scope(str(entry.get("交付范围", "")))
        claimed = scope_range.parse_scope(str(entry.get("已领用范围", "")))
        unclaimed = scope_range.parse_scope(str(entry.get("未领用范围", "")))
        snapshot = self._snapshot(entry)
        available = snapshot.get("可交付记录数")
        reasons: list[str] = []

        if not scope:
            reasons.append("交付范围为空")
        if not snapshot.get("源端已登记"):
            reasons.append(f"源端 {entry.get('源端编号')} 未处于已登记状态")
        if not snapshot.get("目标已登记"):
            reasons.append(f"目标端 {entry.get('目标编号')} 未处于已登记状态")
        if available is None:
            reasons.append("配置快照缺少可交付记录数，无法核对交付范围")
        elif scope:
            # normalize 后的区间按起点升序合并，末段上界即交付范围上界
            upper = scope[-1][1]
            if upper > int(available):
                reasons.append(
                    f"交付范围上界 {upper} 超出配置快照可交付记录数 {available}")
        if not scope_range.is_subset(claimed, scope):
            reasons.append("已领用范围超出交付范围")
        if not scope_range.is_subset(unclaimed, scope):
            reasons.append("未领用范围超出交付范围")
        if scope_range.union(claimed, unclaimed) != scope:
            reasons.append("已领用范围与未领用范围未能还原交付范围")

        verified = not reasons
        return {
            "credential_code": entry.get("凭据编号"),
            "verified": verified,
            "reasons": reasons,
            "stage": entry.get("status"),
            "source_code": entry.get("源端编号"),
            "target_code": entry.get("目标编号"),
            "domain": entry.get("数据域", ""),
            "scope": scope_range.format_scope(scope),
            "claimed": scope_range.format_scope(claimed),
            "unclaimed": scope_range.format_scope(unclaimed),
            "scope_size": scope_range.scope_size(scope),
            "claimed_size": scope_range.scope_size(claimed),
            "available_records": available,
            "snapshot_digest": snapshot.get("快照摘要"),
            "snapshot": snapshot,
        }

    # -- 登记与确认 --

    def _next_start_no(self, target_code: str, batch_code: str = "") -> int:
        """为本目标端新凭据分配起始序号。

        同一批次重复签发凭据（例如交接后旧批需要重新出具）沿用该批次原有的起始序号，
        保证批次在目标端接收流里的位置不因换一份凭据而漂移；否则排在已有未撤销凭据
        覆盖的最大序号之后。
        """
        if batch_code:
            for row in store.rows(self.MODULE):
                if (row.get("目标编号") == target_code
                        and row.get("批次编号") == batch_code
                        and row.get("status") != "已撤销"):
                    return self._start_no(row)
        highest = 0
        for row in store.rows(self.MODULE):
            if row.get("目标编号") != target_code or row.get("status") == "已撤销":
                continue
            ordinals = self._target_ordinals(row)
            if ordinals:
                highest = max(highest, max(ordinals))
        return highest + 1

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记归属凭据：解析交付范围、生成回执并固化配置快照摘要。

        交付范围在登记时就必须是合法区间；批次尚未创建时允许先领凭据，以窗口/批次
        记录条数作为可交付上限，但范围本身不能为空。起始序号缺省时自动排到本目标端
        已有凭据之后，避免同一目标端的两份来源默认认领同一批落地记录。
        """
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        try:
            scope = scope_range.parse_scope(str(values.get("交付范围", "")))
        except ValueError as exc:
            return None, [str(exc)]
        if not scope:
            return None, ["交付范围"]

        rows = store.rows(self.MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1
        }
        entry.update({field: values.get(field, "") for field in self.LIST_FIELDS})
        entry["批次编号"] = str(values.get("批次编号") or "")
        entry["窗口编号"] = str(values.get("窗口编号") or "")
        entry["交付范围"] = scope_range.format_scope(scope)
        entry["已领用范围"] = ""
        entry["未领用范围"] = entry["交付范围"]
        entry["交接策略"] = str(values.get("交接策略") or POLICY_RETAIN)
        if entry["交接策略"] not in HANDOVER_POLICIES:
            return None, [f"交接策略只支持：{'、'.join(HANDOVER_POLICIES)}"]
        entry["status"] = "草稿"
        entry["凭据阶段"] = "草稿"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["回执编号"] = str(values.get("回执编号") or f"RCPT-{entry['凭据编号']}")
        entry.pop("配置快照", None)

        start_no = values.get("起始序号")
        if str(start_no or "").strip():
            try:
                start_no = int(str(start_no).strip())
            except (TypeError, ValueError):
                return None, ["起始序号必须是正整数"]
            if start_no < 1:
                return None, ["起始序号必须是正整数"]
        else:
            start_no = self._next_start_no(
                str(entry["目标编号"]), str(entry["批次编号"]))
        entry["起始序号"] = start_no

        snapshot = self.build_config_snapshot(
            str(entry["源端编号"]), str(entry["目标编号"]),
            entry["批次编号"], entry["窗口编号"])
        entry["_config_snapshot"] = snapshot
        entry["配置快照"] = str(snapshot.get("快照摘要", ""))

        rows.append(entry)
        self._write_jsonl(
            self._data_dir("events") / "ownership_credential_log.jsonl",
            [{"action": "register", "credential_code": entry["凭据编号"],
              "source": entry["源端编号"], "target": entry["目标编号"],
              "domain": entry["数据域"], "scope": entry["交付范围"],
              "snapshot_digest": entry["配置快照"], "at": time.time()}])
        return entry, []

    def issue_credential(
        self,
        source_code: str,
        target_code: str,
        *,
        batch_code: str = "",
        window_code: str = "",
        scope: str | None = None,
        domain: str = "",
        handover_policy: str = POLICY_RETAIN,
    ) -> dict[str, Any]:
        """为一次交付签发凭据；未显式给范围时按配置快照的可交付记录数取全量。"""
        snapshot = self.build_config_snapshot(source_code, target_code, batch_code, window_code)
        available = snapshot.get("可交付记录数")
        resolved_scope = scope
        if not resolved_scope:
            if available is None:
                return {"error": "配置快照缺少可交付记录数，请显式给出交付范围"}
            resolved_scope = f"1-{int(available)}"
        cred_id = "CRED-" + self._compute_checksum(
            f"{source_code}:{target_code}:{batch_code}:{resolved_scope}:{time.time_ns()}"
        )[:12]
        entry, missing = self.create_entry({
            "凭据编号": cred_id,
            "源端编号": source_code,
            "目标编号": target_code,
            "数据域": domain or snapshot.get("数据域", ""),
            "批次编号": batch_code,
            "窗口编号": window_code,
            "交付范围": resolved_scope,
            "交接策略": handover_policy,
        })
        if entry is None:
            return {"error": f"签发凭据失败：{'、'.join(missing)}"}
        verification = self._verify_scope_entry(entry)
        return {
            "credential_code": entry["凭据编号"],
            "credential_id": entry["id"],
            "scope": entry["交付范围"],
            "stage": entry["status"],
            "receipt_code": entry["回执编号"],
            "snapshot_digest": entry["配置快照"],
            "available_records": available,
            "verified": verification["verified"],
            "reasons": verification["reasons"],
        }

    def confirm_credential(self, cred_id: int, confirmed_by: str = "") -> dict[str, Any]:
        """目标端确认登记：凭据进入待领用阶段，并向源端出具回执。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") != "草稿":
            return {"error": f"仅草稿凭据可提交确认，当前阶段为{entry.get('status')}"}
        verification = self._verify_scope_entry(entry)
        entry["status"] = "待领用"
        entry["凭据阶段"] = "待领用"
        entry["pending"] = True
        entry["确认人"] = confirmed_by
        entry["确认时间"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time()))
        receipt = self._write_receipt(entry, "确认登记", extra={
            "confirmed_by": confirmed_by,
            "verified": verification["verified"],
            "reasons": verification["reasons"],
        })
        return {
            "credential_code": entry["凭据编号"],
            "stage": entry["status"],
            "receipt": receipt,
            "verified": verification["verified"],
            "reasons": verification["reasons"],
        }

    def _write_receipt(
        self, entry: dict[str, Any], action: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """把凭据当前阶段、来源、范围和快照摘要写进回执，供操作员事后核对。"""
        receipt = {
            "receipt_code": entry.get("回执编号"),
            "credential_code": entry.get("凭据编号"),
            "action": action,
            "source_code": entry.get("源端编号"),
            "target_code": entry.get("目标编号"),
            "domain": entry.get("数据域", ""),
            "scope": entry.get("交付范围"),
            "claimed": entry.get("已领用范围", ""),
            "unclaimed": entry.get("未领用范围", ""),
            "stage": entry.get("status"),
            "handover_policy": entry.get("交接策略", POLICY_RETAIN),
            "snapshot_digest": entry.get("配置快照", ""),
            "at": time.time(),
        }
        receipt.update(extra or {})
        self._write_jsonl(
            self._data_dir("events") / "ownership_receipt_log.jsonl", [receipt])
        return receipt

    # -- 领用与落地 --

    def claim_scope(self, cred_id: int, scope: str) -> dict[str, Any]:
        """登记领用：从「未领用范围」里划走一段，领用后只有已领用部分可发运。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") not in ("待领用", "已领用", "待重新确认"):
            return {"error": f"当前阶段{entry.get('status')}不允许领用"}
        try:
            requested = scope_range.parse_scope(scope)
        except ValueError as exc:
            return {"error": str(exc)}
        if not requested:
            return {"error": "领用范围为空"}

        full = scope_range.parse_scope(str(entry.get("交付范围", "")))
        claimed = scope_range.parse_scope(str(entry.get("已领用范围", "")))
        unclaimed = scope_range.parse_scope(str(entry.get("未领用范围", "")))
        if not scope_range.is_subset(requested, full):
            return {"error": "领用范围超出交付范围"}
        if not scope_range.is_subset(requested, unclaimed):
            return {"error": "领用范围与已领用范围重叠，不能重复领用"}

        claimed = scope_range.union(claimed, requested)
        unclaimed = scope_range.subtract(unclaimed, requested)
        entry["已领用范围"] = scope_range.format_scope(claimed)
        entry["未领用范围"] = scope_range.format_scope(unclaimed)
        # 领用范围一旦有记录发出，凭据即进入已领用；未领用部分记录在案继续待领用
        entry["status"] = "已领用"
        entry["凭据阶段"] = "已领用"
        entry["pending"] = bool(unclaimed)
        receipt = self._write_receipt(entry, "登记领用", {
            "requested": scope_range.format_scope(requested)})
        verification = self._verify_scope_entry(entry)
        return {
            "credential_code": entry["凭据编号"],
            "stage": entry["status"],
            "claimed": entry["已领用范围"],
            "unclaimed": entry["未领用范围"],
            "receipt": receipt,
            "verified": verification["verified"],
            "reasons": verification["reasons"],
        }

    def mark_landed(self, cred_id: int, scope: str = "") -> dict[str, Any]:
        """确认落地：以已领用范围（或显式给出的落地范围）为准把凭据推进到已落地。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") not in ("已领用",):
            return {"error": f"当前阶段{entry.get('status')}不允许确认落地"}
        claimed = scope_range.parse_scope(str(entry.get("已领用范围", "")))
        if not claimed:
            return {"error": "尚未领用任何范围，不能确认落地"}
        landed = scope_range.parse_scope(scope) if scope else claimed
        if not scope_range.is_subset(landed, claimed):
            return {"error": "落地范围超出已领用范围"}
        entry["已落地范围"] = scope_range.format_scope(landed)
        entry["status"] = "已落地"
        entry["凭据阶段"] = "已落地"
        entry["pending"] = False
        receipt = self._write_receipt(entry, "确认落地", {
            "landed": entry["已落地范围"]})
        return {
            "credential_code": entry["凭据编号"],
            "stage": entry["status"],
            "landed": entry["已落地范围"],
            "receipt": receipt,
        }

    def close_credential(self, cred_id: int) -> dict[str, Any]:
        """结案：已落地或已撤销的凭据转为历史凭据，仍可按批次回查。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") not in ("已落地", "已撤销"):
            return {"error": f"仅已落地或已撤销凭据可结案，当前阶段为{entry.get('status')}"}
        entry["status"] = "已结案"
        entry["凭据阶段"] = "已结案"
        entry["pending"] = False
        entry["结案时间"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time()))
        receipt = self._write_receipt(entry, "确认结案")
        return {
            "credential_code": entry["凭据编号"],
            "stage": entry["status"],
            "receipt": receipt,
        }

    def revoke_credential(self, cred_id: int, reason: str = "") -> dict[str, Any]:
        """撤销未落地的凭据；已落地/已结案的历史事实不可撤销、不可改写。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") in IMMUTABLE_STAGES:
            return {"error": "已落地或已结案的凭据属于历史事实，不能撤销"}
        if entry.get("status") == "已撤销":
            return {"error": "凭据已撤销"}
        entry["status"] = "已撤销"
        entry["凭据阶段"] = "已撤销"
        entry["pending"] = False
        entry["撤销原因"] = reason
        receipt = self._write_receipt(entry, "撤销凭据", {"reason": reason})
        return {"credential_code": entry["凭据编号"], "stage": entry["status"],
                "reason": reason, "receipt": receipt}

    def require_reconfirm(self, cred_id: int, reason: str = "") -> dict[str, Any]:
        """要求发货前重新确认：交接后旧凭据仍在，但发运前必须再过一次确认。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") in IMMUTABLE_STAGES:
            return {"error": "已落地或已结案的凭据无需重新确认"}
        if entry.get("status") == "已撤销":
            return {"error": "已撤销凭据不能重新确认"}
        entry["交接策略"] = POLICY_RECONFIRM
        entry["status"] = "待重新确认"
        entry["凭据阶段"] = "待重新确认"
        entry["pending"] = True
        receipt = self._write_receipt(entry, "要求重新确认", {"reason": reason})
        return {"credential_code": entry["凭据编号"], "stage": entry["status"],
                "handover_policy": entry["交接策略"], "receipt": receipt}

    def confirm_before_ship(self, cred_id: int) -> dict[str, Any]:
        """发货前重新确认通过：凭据回到已领用阶段，可继续发运。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if entry.get("status") != "待重新确认":
            return {"error": f"当前阶段{entry.get('status')}无需重新确认"}
        entry["status"] = "已领用" if entry.get("已领用范围") else "待领用"
        entry["凭据阶段"] = entry["status"]
        entry["pending"] = not bool(entry.get("已领用范围"))
        receipt = self._write_receipt(entry, "确认发运")
        return {"credential_code": entry["凭据编号"], "stage": entry["status"],
                "receipt": receipt}

    # -- 交接 --

    def handover(
        self,
        cred_id: int,
        new_source_code: str,
        *,
        policy: str = POLICY_RETAIN,
        new_batch_code: str = "",
        new_window_code: str = "",
        new_scope: str | None = None,
        operator: str = "",
    ) -> dict[str, Any]:
        """把数据域归属从旧源端交接给新源端，旧凭据按策略保留或要求重新确认。

        关键约束：
        - 交接不会替新接收方撤销旧凭据（旧凭据的撤销必须单独发起）。
        - 已落地/已结案的旧批不因归属切换被改写，只追加一条交接留痕。
        - 新来源若同时提交新窗口，会拿到一条与旧凭据互不覆盖的新凭据。
        """
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        if policy not in HANDOVER_POLICIES:
            return {"error": f"交接策略只支持：{'、'.join(HANDOVER_POLICIES)}"}
        if entry.get("status") == "已撤销":
            return {"error": "已撤销凭据不能参与交接"}

        immutable = entry.get("status") in IMMUTABLE_STAGES
        previous_source = entry.get("源端编号")
        previous_scope = entry.get("交付范围")
        previous_batch = entry.get("批次编号")
        previous_stage = entry.get("status")
        requires_reconfirm = False

        if not immutable:
            entry["交接策略"] = policy
            entry["交接至源端"] = new_source_code
            if policy == POLICY_RECONFIRM:
                entry["status"] = "待重新确认"
                entry["凭据阶段"] = "待重新确认"
                entry["pending"] = True
                requires_reconfirm = True
            # 保留到结案：旧凭据阶段不变，已领用范围继续有效
        # 已落地/已结案的旧批保持原样：不写回任何字段，交接只进留痕日志

        handover_record = {
            "credential_code": entry["凭据编号"],
            "target_code": entry.get("目标编号"),
            "domain": entry.get("数据域", ""),
            "previous_source": previous_source,
            "new_source": new_source_code,
            "previous_batch": previous_batch,
            "previous_scope": previous_scope,
            "previous_stage": previous_stage,
            "policy": policy,
            "requires_reconfirm": requires_reconfirm,
            "old_batch_rewritten": False,
            "old_stage_preserved": immutable,
            "operator": operator,
            "at": time.time(),
        }
        self._write_jsonl(
            self._data_dir("events") / "ownership_handover_log.jsonl", [handover_record])

        receipt = self._write_receipt(
            entry, "执行交接", {
                "previous_source": previous_source,
                "new_source": new_source_code,
                "policy": policy,
                "requires_reconfirm": requires_reconfirm,
                "old_batch_rewritten": False,
            })
        if not immutable:
            entry["_handover_record"] = handover_record

        successor = None
        if new_source_code != previous_source and (new_batch_code or new_window_code or new_scope):
            successor = self.issue_credential(
                new_source_code,
                str(entry.get("目标编号", "")),
                batch_code=new_batch_code,
                window_code=new_window_code,
                scope=new_scope,
                domain=str(entry.get("数据域", "")),
                handover_policy=policy,
            )

        return {
            "credential_code": entry["凭据编号"],
            "previous_source": previous_source,
            "new_source": new_source_code,
            "old_stage": entry.get("status"),
            "old_stage_preserved": immutable,
            "old_batch_rewritten": False,
            "handover_policy": entry.get("交接策略", policy),
            "requires_reconfirm": requires_reconfirm,
            "handover": handover_record,
            "successor": successor,
            "receipt": receipt,
        }

    # -- 落地核对与归属反查 --

    def verify_landed(self, cred_id: int) -> dict[str, Any]:
        """核对实际落地记录与凭据阶段：逐记录判断是否在已领用范围内。

        从暂存落地产物读取真实落地的记录：有 ``target_no`` 的目标端序号优先，
        没有时退回源记录号 ``line_no``。两者都与凭据的交付/领用范围比对：
        落地超出凭据范围的记录没有归属，属于归属切换期最需要拦下的情况。
        """
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        batch_code = str(entry.get("批次编号", ""))
        scope = scope_range.parse_scope(str(entry.get("交付范围", "")))
        claimed = scope_range.parse_scope(str(entry.get("已领用范围", "")))
        landed_lines = self._landed_lines(batch_code)
        landed_set = set(landed_lines)

        start = self._start_no(entry)
        # 落地记录按目标端序号标注时，凭据范围同样按目标端序号核；否则按源记录号核。
        if self._landed_uses_target_no(batch_code):
            scope_set = set(self._target_ordinals(entry))
            claimed_set = set(start + no - 1
                              for no in scope_range.expand(claimed))
        else:
            scope_set = set(scope_range.expand(scope))
            claimed_set = set(scope_range.expand(claimed))
        expected = claimed_set or scope_set
        unowned = sorted(landed_set - scope_set)
        outside_claim = sorted(landed_set - claimed_set) if claimed_set else []
        missing = sorted(expected - landed_set)

        stage = str(entry.get("status"))
        stage_ok = stage in ("已领用", "已落地", "已结案")
        verified = not unowned and not missing and stage_ok
        return {
            "credential_code": entry.get("凭据编号"),
            "batch_code": batch_code,
            "stage": stage,
            "stage_ok": stage_ok,
            "verified": verified,
            "landed_count": len(landed_set),
            "landed_lines": sorted(landed_set),
            "scope": scope_range.format_scope(scope),
            "claimed": scope_range.format_scope(claimed),
            "expected_count": len(expected),
            "missing_lines": missing,
            "unowned_lines": unowned,
            "outside_claim_lines": outside_claim,
            "artifact_found": bool(landed_lines),
        }

    def _landed_records(self, batch_code: str) -> list[dict[str, Any]]:
        """读取暂存落地文件中属于该批次的记录。

        以 store 里该批次的暂存分区记录为准定位产物文件，避免同名批次的历史
        落地产物混进来；没有暂存登记时才退回按批次号扫描。
        """
        if not batch_code:
            return []
        records: list[dict[str, Any]] = []
        staging_dir = self._data_dir("staging")
        staging_codes = [
            str(row.get("暂存编号") or row.get("落地标识") or "")
            for row in store.rows("staging_area")
            if row.get("批次编号") == batch_code
        ]
        staging_codes = [code for code in staging_codes if code]
        files = (
            [staging_dir / f"{code}.jsonl" for code in staging_codes]
            if staging_codes else list(staging_dir.glob(f"*{batch_code}*.jsonl"))
        )
        for staging_file in files:
            for record in self._read_jsonl(staging_file):
                if str(record.get("batch_code", "")) not in ("", batch_code):
                    continue
                records.append(record)
        return records

    def _landed_uses_target_no(self, batch_code: str) -> bool:
        return any("target_no" in record for record in self._landed_records(batch_code))

    def _landed_lines(self, batch_code: str) -> list[int]:
        """读取落地记录的核对序号：优先目标端序号，缺失时退回源记录号。"""
        lines: list[int] = []
        records = self._landed_records(batch_code)
        uses_target = any("target_no" in record for record in records)
        for record in records:
            value = record.get("target_no") if uses_target else record.get("line_no")
            try:
                lines.append(int(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(lines))

    def resolve_delivery(self, target_code: str, record_range: str) -> dict[str, Any]:
        """按目标端接收序号反查归属：这条落地记录属于哪个源端、哪份凭据。

        凭据的「起始序号」决定这份源数据在目标端接收流里的起始位置，交付范围内的
        记录按顺序映射为目标端序号 ``起始序号 + (记录序号 - 1)``。同一目标端同时收到
        两个来源时，同一目标序号会被两份凭据同时认领，这里显式标记为重叠冲突，
        提示目标端核对实际接收的是哪一份源数据。
        """
        try:
            requested = scope_range.parse_scope(record_range)
        except ValueError as exc:
            return {"error": str(exc)}
        requested_numbers = scope_range.expand(requested)
        if not requested_numbers:
            return {"error": "核对范围为空"}

        candidates = [
            row for row in store.rows(self.MODULE)
            if row.get("目标编号") == target_code and row.get("status") != "已撤销"
        ]
        # 预先展开每份凭据覆盖的目标端序号，避免逐条记录重复解析
        coverage: list[tuple[int, dict[str, Any]]] = [
            (ordinal, row)
            for row in candidates
            for ordinal in self._target_ordinals(row)
        ]

        attributions: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        unowned: list[int] = []
        by_source: dict[str, list[int]] = {}
        for number in requested_numbers:
            owners = [row for ordinal, row in coverage if ordinal == number]
            if not owners:
                unowned.append(number)
                continue
            # 同一来源同批次可能因交接重新出具多份凭据，这不算归属冲突；
            # 只有两个不同来源同时认领同一目标序号才是需要核对的交接重叠。
            sources = {str(row.get("源端编号")) for row in owners}
            cross_source = len(sources) > 1
            attributions.append({
                "target_no": number,
                "credential_code": owners[0].get("凭据编号") if not cross_source else None,
                "source_code": owners[0].get("源端编号") if not cross_source else None,
                "candidates": [
                    {"credential_code": row.get("凭据编号"),
                     "source_code": row.get("源端编号"),
                     "stage": row.get("status")}
                    for row in owners
                ],
                "conflict": cross_source,
            })
            if cross_source:
                conflicts.append({"target_no": number, "candidates": [
                    {"credential_code": row.get("凭据编号"),
                     "source_code": row.get("源端编号")}
                    for row in owners]})
            else:
                by_source.setdefault(str(owners[0].get("源端编号")), []).append(number)

        return {
            "target_code": target_code,
            "requested": scope_range.format_scope(requested),
            "records": attributions,
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "unowned": unowned,
            "unowned_count": len(unowned),
            "by_source": {
                source: self._numbers_to_text(numbers)
                for source, numbers in by_source.items()
            },
            "distinct_sources": sorted(by_source),
        }

    @staticmethod
    def _start_no(entry: dict[str, Any]) -> int:
        try:
            value = int(str(entry.get("起始序号") or 1).strip())
        except (TypeError, ValueError):
            return 1
        return max(1, value)

    def _target_ordinals(self, entry: dict[str, Any]) -> list[int]:
        """把凭据的交付范围映射成目标端接收序号。"""
        start = self._start_no(entry)
        return [start + record_no - 1
                for record_no in scope_range.expand(
                    scope_range.parse_scope(str(entry.get("交付范围", ""))))]

    @staticmethod
    def _numbers_to_text(numbers: list[int]) -> str:
        return scope_range.format_scope(
            [(number, number) for number in sorted(set(numbers))])

    def receipt_for(self, cred_id: int) -> dict[str, Any]:
        """输出目标端回执：把凭据阶段、来源范围、配置快照和落地核对合并到一份回执。"""
        entry = self.get_entry(cred_id)
        if entry is None:
            return {"error": f"批次归属凭据 {cred_id} 不存在"}
        verification = self._verify_scope_entry(entry)
        landed = self.verify_landed(cred_id)
        return {
            "receipt_code": entry.get("回执编号"),
            "credential_code": entry.get("凭据编号"),
            "source_code": entry.get("源端编号"),
            "target_code": entry.get("目标编号"),
            "domain": entry.get("数据域", ""),
            "batch_code": entry.get("批次编号", ""),
            "stage": entry.get("status"),
            "handover_policy": entry.get("交接策略", POLICY_RETAIN),
            "scope_verified": verification["verified"],
            "scope_reasons": verification["reasons"],
            "landed_verified": landed["verified"],
            "landed_count": landed.get("landed_count", 0),
            "snapshot_digest": entry.get("配置快照", ""),
            "history_immutable": entry.get("status") in IMMUTABLE_STAGES,
        }

    def history(
        self,
        *,
        source_code: str | None = None,
        domain: str | None = None,
        target_code: str | None = None,
        include_active: bool = False,
    ) -> dict[str, Any]:
        """回查历史凭据；默认只看已落地/已结案，用于确认旧批仍可追溯。"""
        rows = list(store.rows(self.MODULE))
        if source_code:
            rows = [row for row in rows if row.get("源端编号") == source_code]
        if domain:
            rows = [row for row in rows if row.get("数据域") == domain]
        if target_code:
            rows = [row for row in rows if row.get("目标编号") == target_code]
        if not include_active:
            rows = [row for row in rows if row.get("status") in IMMUTABLE_STAGES]
        items = [
            {
                "credential_code": row.get("凭据编号"),
                "source_code": row.get("源端编号"),
                "target_code": row.get("目标编号"),
                "domain": row.get("数据域", ""),
                "batch_code": row.get("批次编号", ""),
                "scope": row.get("交付范围"),
                "claimed": row.get("已领用范围", ""),
                "stage": row.get("status"),
                "handover_policy": row.get("交接策略", POLICY_RETAIN),
                "receipt_code": row.get("回执编号", ""),
                "immutable": row.get("status") in IMMUTABLE_STAGES,
            }
            for row in rows
        ]
        return {"items": items, "total": len(items),
                "scope": "已落地与已结案" if not include_active else "全部阶段"}

    # -- 传输侧发运闸门 --

    def _active_for_batch(self, batch_code: str, window_code: str) -> list[dict[str, Any]]:
        """找出与批次/窗口关联且未撤销的凭据。"""
        rows: list[dict[str, Any]] = []
        for row in store.rows(self.MODULE):
            if row.get("status") == "已撤销":
                continue
            if batch_code and row.get("批次编号") == batch_code:
                rows.append(row)
            elif window_code and row.get("窗口编号") == window_code:
                rows.append(row)
        return rows

    def shipment_gate(self, batch_code: str, window_code: str = "") -> dict[str, Any]:
        """发运闸门：告诉传输批次“这份数据能发哪一段”。

        - 没有登记凭据时返回 ``unrestricted``，保持旧批次行为不变。
        - 有凭据时只允许发运「已领用范围」；未领用部分必须留在源端。
        - 凭据处于「待重新确认」时禁止发运，必须先完成发货前重新确认。
        - 同一批次有多份凭据时取并集，避免交接期误拦新来源。
        """
        creds = self._active_for_batch(batch_code, window_code)
        if not creds:
            return {"mode": "unrestricted", "allowed": None, "reasons": [],
                    "credentials": []}
        blocked = [c for c in creds if c.get("status") == "待重新确认"]
        if blocked:
            return {
                "mode": "blocked",
                "allowed": None,
                "reasons": [
                    f"凭据 {c.get('凭据编号')} 待发货前重新确认，禁止发运" for c in blocked],
                "credentials": [c.get("凭据编号") for c in creds],
            }
        claimed: list[tuple[int, int]] = []
        for cred in creds:
            claimed = scope_range.union(
                claimed, scope_range.parse_scope(str(cred.get("已领用范围", ""))))
        if not claimed:
            return {
                "mode": "blocked",
                "allowed": None,
                "reasons": ["凭据尚未登记领用范围，未领用部分不能发运"],
                "credentials": [c.get("凭据编号") for c in creds],
            }
        return {
            "mode": "claimed",
            "allowed": claimed,
            "allowed_text": scope_range.format_scope(claimed),
            "reasons": [],
            "credentials": [c.get("凭据编号") for c in creds],
        }

    # -- 通用动作分派 --

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """把通用动作接口映射到凭据生命周期方法，非法阶段给出可读原因。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action in ("提交确认", "确认登记"):
            result = self.confirm_credential(entry_id)
        elif action == "登记领用":
            unclaimed = str(entry.get("未领用范围", ""))
            if not unclaimed:
                return None, "没有可领用的范围"
            result = self.claim_scope(entry_id, unclaimed)
        elif action == "确认落地":
            result = self.mark_landed(entry_id)
        elif action == "确认结案":
            result = self.close_credential(entry_id)
        elif action == "撤销凭据":
            result = self.revoke_credential(entry_id)
        elif action == "要求重新确认":
            result = self.require_reconfirm(entry_id)
        elif action == "确认发运":
            result = self.confirm_before_ship(entry_id)
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        if "error" in result:
            return None, str(result["error"])
        updated = self.get_entry(entry_id) or entry
        return updated, f"{self.ENTITY}已{action}，当前阶段{updated.get('status')}"


def bootstrap_seed_snapshots(rows: list[dict[str, Any]]) -> None:
    """为示例凭据补算配置快照摘要，让起服务后回执与范围校验立即可用。

    seed 数据是静态字典，不在导入期调用服务，因此由 store 在初始化后传入表数据补一遍；
    已保存快照的凭据跳过，运行期新签发的凭据不受影响。
    """
    service = OwnershipCredentialService()
    for entry in rows:
        if entry.get("配置快照"):
            continue
        snapshot = service.build_config_snapshot(
            str(entry.get("源端编号", "")),
            str(entry.get("目标编号", "")),
            str(entry.get("批次编号", "")),
            str(entry.get("窗口编号", "")),
        )
        entry["_config_snapshot"] = snapshot
        entry["配置快照"] = str(snapshot.get("快照摘要", ""))
