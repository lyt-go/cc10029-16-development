"""批次数据归属凭据：交付范围校验、领用区分、交接与落地核对。

覆盖场景：源端 A 先领凭据、订单域交接给源端 B、B 同时提交新窗口；
A 的旧批在交接后仍可发运/回查，已落地旧批不被改写，两来源在新窗口上互不覆盖。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services.ownership_credential import (
    POLICY_RECONFIRM,
    POLICY_RETAIN,
    OwnershipCredentialService,
)
from app.services.scope_range import (
    format_scope,
    is_subset,
    parse_scope,
    scope_size,
    subtract,
)
from app.services.staging_area import StagingAreaService
from app.services.transfer_batch import TransferBatchService


@pytest.fixture(autouse=True)
def reset_store(tmp_path, monkeypatch):
    store._tables = {
        name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
    }
    # 产物目录按测试隔离：base._data_dir 读取模块级 DATA_ROOT
    import app.services.base as base_module
    monkeypatch.setattr(base_module, "DATA_ROOT", tmp_path / "data")
    yield


def _seed_source_and_target(source_code="SOUR-A", target_code="TARG-T1"):
    store.rows("source_register").append({
        "id": 100,
        "status": "已登记",
        "pending": False,
        "abnormal": False,
        "源端编号": source_code,
        "系统名称": "订单源端",
        "数据域": "订单域",
        "记录格式": "JSON",
        "编码方式": "UTF-8",
        "日增量级": "10万",
        "抽取方式": "增量抽取",
        "登记游标": "0",
    })
    store.rows("target_register").append({
        "id": 100,
        "status": "已登记",
        "pending": False,
        "abnormal": False,
        "目标编号": target_code,
        "系统名称": "订单目标端",
        "接收格式": "Parquet",
        "落地方式": "分区落地",
        "批量上限": "1000",
        "确认模式": "自动",
        "回执格式": "JSON",
        "登记游标": "0",
    })
    store.rows("extract_window").append({
        "id": 100,
        "status": "已闭合",
        "pending": False,
        "abnormal": False,
        "窗口编号": "EXTR-A-OLD",
        "源端编号": source_code,
        "增量字段": "updated_at",
        "窗口起点": "2026-11-01",
        "窗口终点": "2026-11-02",
        "抽取条数": "100",
        "窗口类型": "增量抽取",
        "推进水位": "100",
    })


def _setup_batch(batch_code="TRAN-OWN-1", window_code="EXTR-A-OLD", count=100,
                 target_code="TARG-T1"):
    store.rows("transfer_batch").append({
        "id": 100,
        "status": "待传输",
        "pending": True,
        "abnormal": False,
        "批次编号": batch_code,
        "窗口编号": window_code,
        "目标编号": target_code,
        "记录条数": str(count),
        "字节总量": "0",
        "分片大小": "1000",
        "传输状态": "待传输",
        "批次水位": "0",
    })
    return batch_code


class TestScopeRange:
    """区间运算：交付范围、已领用、未领用之间的基本关系。"""

    def test_parse_and_format_roundtrip(self):
        assert format_scope(parse_scope("1-40,61-100")) == "1-40,61-100"
        assert scope_size(parse_scope("1-40,61-100")) == 80

    def test_subtract_marks_unclaimed(self):
        remaining = subtract(parse_scope("1-100"), parse_scope("1-40,61-80"))
        assert format_scope(remaining) == "41-60,81-100"

    def test_subset(self):
        assert is_subset(parse_scope("41-60"), parse_scope("1-100"))
        assert not is_subset(parse_scope("1-120"), parse_scope("1-100"))

    def test_rejects_bad_scope(self):
        with pytest.raises(ValueError):
            parse_scope("0-10")


class TestCredentialScopeVerification:
    """配置快照校验：目标端能核对实际接收哪一份源数据。"""

    def test_snapshot_carries_registration_config(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        snapshot = svc.build_config_snapshot("SOUR-A", "TARG-T1", batch_code="")
        assert snapshot["源端已登记"] is True
        assert snapshot["目标已登记"] is True
        assert snapshot["目标接收格式"] == "Parquet"
        assert snapshot["数据域"] == "订单域"
        assert snapshot["快照摘要"]

    def test_scope_verified_against_snapshot(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(), scope="1-80")
        assert issued["verified"] is True, issued["reasons"]
        verification = svc.verify_scope(issued["credential_id"])
        assert verification["verified"] is True
        assert verification["available_records"] == 100
        assert verification["scope"] == "1-80"

    def test_scope_beyond_snapshot_rejected(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(), scope="1-120")
        assert issued["verified"] is False
        assert any("超出配置快照" in reason for reason in issued["reasons"])

    def test_unregistered_source_fails_scope(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential("SOUR-MISSING", "TARG-T1", scope="1-10")
        assert issued["verified"] is False


class TestClaimDistinguishesClaimedAndUnclaimed:
    """交付资格要能区分已领用范围与未领用范围。"""

    def test_partial_claim_leaves_remainder(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(), scope="1-100")
        svc.confirm_credential(issued["credential_id"])
        result = svc.claim_scope(issued["credential_id"], "1-40")
        assert result["claimed"] == "1-40"
        assert result["unclaimed"] == "41-100"
        assert result["stage"] == "已领用"
        assert result["verified"] is True

    def test_cannot_claim_twice(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(), scope="1-100")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-40")
        again = svc.claim_scope(issued["credential_id"], "1-20")
        assert "error" in again

    def test_cannot_claim_beyond_scope(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(), scope="1-40")
        svc.confirm_credential(issued["credential_id"])
        result = svc.claim_scope(issued["credential_id"], "1-80")
        assert "error" in result


class TestTransferConsumesClaimedScopeOnly:
    """发起传输时按凭据里的来源、范围和批次领用数据发运。"""

    def test_transfer_only_ships_claimed_records(self):
        _seed_source_and_target()
        batch = _setup_batch(count=100)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=batch, window_code="EXTR-A-OLD", scope="1-100")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-40")

        tb = TransferBatchService()
        batch_id = next(
            r["id"] for r in store.rows("transfer_batch") if r["批次编号"] == batch)
        result = tb.start_transfer(batch_id, chunk_size=10)
        assert "error" not in result, result
        assert result["records_transferred"] == 40
        assert result["ownership_gate"]["mode"] == "claimed"
        assert result["ownership_gate"]["allowed"] == "1-40"

    def test_transfer_blocked_without_claim(self):
        _seed_source_and_target()
        batch = _setup_batch(count=50)
        svc = OwnershipCredentialService()
        svc.issue_credential("SOUR-A", "TARG-T1", batch_code=batch, scope="1-50")
        tb = TransferBatchService()
        batch_id = next(
            r["id"] for r in store.rows("transfer_batch") if r["批次编号"] == batch)
        result = tb.start_transfer(batch_id)
        assert "error" in result
        assert "领用" in result["error"]

    def test_transfer_blocked_when_reconfirm_required(self):
        _seed_source_and_target()
        batch = _setup_batch(count=50)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=batch, scope="1-50")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-50")
        svc.require_reconfirm(issued["credential_id"], "归属切换")
        tb = TransferBatchService()
        batch_id = next(
            r["id"] for r in store.rows("transfer_batch") if r["批次编号"] == batch)
        blocked = tb.start_transfer(batch_id)
        assert "error" in blocked
        svc.confirm_before_ship(issued["credential_id"])
        ok = tb.start_transfer(batch_id)
        assert "error" not in ok, ok


class TestHandover:
    """订单域从源 A 交接给源 B：旧凭据保留或重新确认，新来源同时提交新窗口。"""

    def _prepare_a(self, policy_scope="1-100"):
        _seed_source_and_target()
        _seed_source_and_target(source_code="SOUR-B", target_code="TARG-T1")
        store.rows("extract_window").append({
            "id": 101,
            "status": "待抽取",
            "pending": True,
            "abnormal": False,
            "窗口编号": "EXTR-B-NEW",
            "源端编号": "SOUR-B",
            "增量字段": "updated_at",
            "窗口起点": "2026-11-10",
            "窗口终点": "2026-11-11",
            "抽取条数": "60",
            "窗口类型": "增量抽取",
            "推进水位": "0",
        })
        batch = _setup_batch(count=100)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=batch,
            window_code="EXTR-A-OLD", scope=policy_scope,
            domain="订单域", handover_policy=POLICY_RETAIN)
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], policy_scope)
        return svc, issued

    def test_handover_retain_keeps_old_stage(self):
        svc, issued = self._prepare_a()
        result = svc.handover(issued["credential_id"], "SOUR-B", policy=POLICY_RETAIN)
        assert result["old_batch_rewritten"] is False
        assert result["requires_reconfirm"] is False
        assert result["old_stage"] == "已领用"
        # A 的旧批仍可发运
        gate = svc.shipment_gate("TRAN-OWN-1")
        assert gate["mode"] == "claimed"

    def test_handover_reconfirm_blocks_until_confirmed(self):
        svc, issued = self._prepare_a()
        result = svc.handover(issued["credential_id"], "SOUR-B", policy=POLICY_RECONFIRM)
        assert result["requires_reconfirm"] is True
        assert result["old_stage"] == "待重新确认"
        gate = svc.shipment_gate("TRAN-OWN-1")
        assert gate["mode"] == "blocked"
        svc.confirm_before_ship(issued["credential_id"])
        assert svc.shipment_gate("TRAN-OWN-1")["mode"] == "claimed"

    def test_new_source_new_window_gets_separate_credential(self):
        svc, issued = self._prepare_a()
        result = svc.handover(
            issued["credential_id"], "SOUR-B", policy=POLICY_RETAIN,
            new_batch_code="TRAN-B-1", new_window_code="EXTR-B-NEW",
            new_scope="1-60", operator="ops")
        successor = result["successor"]
        assert successor and successor["credential_code"] != issued["credential_code"]
        assert successor["scope"] == "1-60"
        # 新凭据起始序号排在旧凭据之后，两者不重叠
        old = svc.get_entry(issued["credential_id"])
        new = svc.get_entry(successor["credential_id"])
        assert new["起始序号"] == 101
        assert svc.resolve_delivery("TARG-T1", "101-160")["conflict_count"] == 0
        assert svc.resolve_delivery("TARG-T1", "1-160")["distinct_sources"] == ["SOUR-A", "SOUR-B"]

    def test_handover_does_not_revoke_for_new_receiver(self):
        svc, issued = self._prepare_a()
        svc.handover(issued["credential_id"], "SOUR-B", policy=POLICY_RETAIN,
                     new_batch_code="TRAN-B-1", new_window_code="EXTR-B-NEW", new_scope="1-60")
        assert svc.get_entry(issued["credential_id"])["status"] != "已撤销"

    def test_same_batch_two_sources_flagged_as_overlap(self):
        """同一物理批次在交接期被两个来源同时认领时，目标端序号重叠要能报出来。"""
        svc, issued = self._prepare_a(policy_scope="1-100")
        # 新接收方为同一批次另行出具凭据，沿用该批次原有的目标端起始序号
        successor = svc.issue_credential(
            "SOUR-B", "TARG-T1", batch_code="TRAN-OWN-1",
            window_code="EXTR-B-NEW", scope="1-60", domain="订单域")
        new = svc.get_entry(successor["credential_id"])
        old = svc.get_entry(issued["credential_id"])
        assert new["起始序号"] == old["起始序号"] == 1

        resolved = svc.resolve_delivery("TARG-T1", "1-60")
        assert resolved["conflict_count"] == 60
        assert sorted(resolved["distinct_sources"]) == []
        assert set(item["candidates"][0]["source_code"] for item in resolved["conflicts"]) == {
            "SOUR-A"}
        sources = {
            candidate["source_code"]
            for item in resolved["conflicts"]
            for candidate in item["candidates"]
        }
        assert sources == {"SOUR-A", "SOUR-B"}

    def test_same_source_reissued_credential_not_a_conflict(self):
        """同一来源重复出具凭据只是补发，不应被当成归属冲突。"""
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        first = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(count=20), scope="1-20")
        second = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code="TRAN-OWN-1", scope="1-20")
        resolved = svc.resolve_delivery("TARG-T1", "1-20")
        assert resolved["conflict_count"] == 0
        assert resolved["distinct_sources"] == ["SOUR-A"]
        assert first["credential_code"] != second["credential_code"]

    def test_landed_batch_not_rewritten_by_handover(self):
        _seed_source_and_target()
        batch = _setup_batch(count=20)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential("SOUR-A", "TARG-T1", batch_code=batch, scope="1-20")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-20")
        svc.mark_landed(issued["credential_id"])
        svc.close_credential(issued["credential_id"])
        before = dict(svc.get_entry(issued["credential_id"]))

        result = svc.handover(issued["credential_id"], "SOUR-B", policy=POLICY_RECONFIRM)
        after = svc.get_entry(issued["credential_id"])
        assert result["old_stage_preserved"] is True
        assert result["old_batch_rewritten"] is False
        assert after["status"] == "已结案" == before["status"]
        assert after["交付范围"] == before["交付范围"]
        assert after["已领用范围"] == before["已领用范围"]
        # 已结案记录不新增任何交接字段
        assert "交接至源端" not in after
        assert "交接策略" not in after or after["交接策略"] == before["交接策略"]

    def test_landed_scope_cannot_be_revoked(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(count=10), scope="1-10")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-10")
        svc.mark_landed(issued["credential_id"])
        assert "error" in svc.revoke_credential(issued["credential_id"])


class TestLandedVerificationAndHistory:
    """核对实际落地记录和凭据阶段，已完成的历史 A 批仍能回查。"""

    def test_landed_matches_claimed_scope(self):
        _seed_source_and_target()
        batch = _setup_batch(count=100)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=batch, window_code="EXTR-A-OLD", scope="1-100")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-40")

        tb = TransferBatchService()
        batch_id = next(
            r["id"] for r in store.rows("transfer_batch") if r["批次编号"] == batch)
        tb.start_transfer(batch_id, chunk_size=10)

        landed = svc.verify_landed(issued["credential_id"])
        assert landed["landed_count"] == 40
        assert landed["missing_lines"] == []
        assert landed["unowned_lines"] == []
        assert landed["verified"] is True

    def test_landed_mid_range_claim_keeps_target_ordinals(self):
        """只领用中间一段时，落地记录仍应落在凭据声明的目标端序号上。"""
        _seed_source_and_target()
        batch = _setup_batch(count=50)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=batch, window_code="EXTR-A-OLD", scope="1-50")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "21-30")

        tb = TransferBatchService()
        batch_id = next(
            r["id"] for r in store.rows("transfer_batch") if r["批次编号"] == batch)
        tb.start_transfer(batch_id, chunk_size=5)

        landed = svc.verify_landed(issued["credential_id"])
        assert landed["landed_lines"] == list(range(21, 31))
        assert landed["missing_lines"] == []
        assert landed["unowned_lines"] == []
        assert landed["verified"] is True

        resolved = svc.resolve_delivery("TARG-T1", "21-30")
        assert resolved["distinct_sources"] == ["SOUR-A"]
        assert resolved["conflict_count"] == 0

    def test_landed_detects_unowned_records(self):
        _seed_source_and_target()
        batch = _setup_batch(count=20)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential("SOUR-A", "TARG-T1", batch_code=batch, scope="1-10")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-10")
        # 落地 20 条，但凭据只覆盖 1-10，多出的记录没有归属
        StagingAreaService().land_batch(batch, 20)
        landed = svc.verify_landed(issued["credential_id"])
        assert landed["unowned_lines"] == list(range(11, 21))
        assert landed["verified"] is False

    def test_history_returns_completed_batches(self):
        _seed_source_and_target()
        batch = _setup_batch(count=20)
        svc = OwnershipCredentialService()
        issued = svc.issue_credential("SOUR-A", "TARG-T1", batch_code=batch, scope="1-20")
        svc.confirm_credential(issued["credential_id"])
        svc.claim_scope(issued["credential_id"], "1-20")
        svc.mark_landed(issued["credential_id"])
        svc.close_credential(issued["credential_id"])

        history = svc.history(source_code="SOUR-A")
        assert history["total"] == 1
        assert history["items"][0]["stage"] == "已结案"
        assert history["items"][0]["batch_code"] == batch

    def test_receipt_summarises_stage_and_scope(self):
        _seed_source_and_target()
        svc = OwnershipCredentialService()
        issued = svc.issue_credential(
            "SOUR-A", "TARG-T1", batch_code=_setup_batch(count=30), scope="1-30")
        receipt = svc.receipt_for(issued["credential_id"])
        assert receipt["stage"] == "草稿"
        assert receipt["scope_verified"] is True
        assert receipt["snapshot_digest"]
