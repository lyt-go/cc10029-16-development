"""批次归属凭据接口：签发、配置快照校验、领用、交接、落地核对与历史回查。

这些接口承载凭据的领域动作，统一资源路由只负责通用列表与登记，因此本路由要
在通用 ``/api/{module}/{entry_id}`` 之前注册，避免固定子路径被整数 ID 抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.ownership_credential import OwnershipCredentialService

router = APIRouter(prefix="/api/ownership_credential", tags=["批次归属凭据"])


def _service() -> OwnershipCredentialService:
    return OwnershipCredentialService()


def _require(cred_id: int) -> dict[str, Any]:
    entry = _service().get_entry(cred_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"批次归属凭据 {cred_id} 不存在")
    return entry


def _resolve_id(credential_code: str) -> int:
    for row in _service().list_entries(size=1_000_000)[0]:
        if row.get("凭据编号") == credential_code:
            return int(row["id"])
    raise HTTPException(status_code=404, detail=f"批次归属凭据 {credential_code} 不存在")


def _fail(payload: dict[str, Any]) -> ActionResult:
    return ActionResult(ok=False, message=str(payload.get("error") or "操作失败"))


class IssuePayload(BaseModel):
    source_code: str
    target_code: str
    batch_code: str = ""
    window_code: str = ""
    scope: str | None = None
    domain: str = ""
    handover_policy: str = "保留到结案"


class ClaimPayload(BaseModel):
    scope: str


class HandoverPayload(BaseModel):
    new_source_code: str
    policy: str = "保留到结案"
    new_batch_code: str = ""
    new_window_code: str = ""
    new_scope: str | None = None
    operator: str = ""


class CredentialRef(BaseModel):
    credential_code: str


@router.post("/issue", response_model=ActionResult)
def issue(payload: IssuePayload) -> ActionResult:
    """签发凭据：按配置快照的可交付记录数确定交付范围，并固化快照摘要。"""
    result = _service().issue_credential(
        payload.source_code,
        payload.target_code,
        batch_code=payload.batch_code,
        window_code=payload.window_code,
        scope=payload.scope,
        domain=payload.domain,
        handover_policy=payload.handover_policy,
    )
    if "error" in result:
        return _fail(result)
    return ActionResult(
        ok=True,
        message=f"已签发凭据 {result['credential_code']}，交付范围 {result['scope']}，"
                f"回执 {result['receipt_code']}",
        entry=result,
    )


@router.get("/resolve")
def resolve(
    target_code: str = Query(..., description="目标端编号"),
    record_range: str = Query(..., description="目标端接收序号范围，如 1-50"),
) -> dict[str, Any]:
    """按目标端接收序号反查归属；两个来源同时覆盖时给出重叠冲突。"""
    result = _service().resolve_delivery(target_code, record_range)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/history")
def history(
    source_code: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    target_code: str | None = Query(default=None),
    include_active: bool = Query(default=False),
) -> dict[str, Any]:
    """回查历史凭据，默认只看已落地/已结案，确认旧批仍可追溯。"""
    return _service().history(
        source_code=source_code, domain=domain,
        target_code=target_code, include_active=include_active)


@router.post("/{cred_id:int}/confirm", response_model=ActionResult)
def confirm(cred_id: int, payload: CredentialRef | None = None) -> ActionResult:
    """目标端确认登记并出具回执。"""
    _require(cred_id)
    result = _service().confirm_credential(cred_id)
    if "error" in result:
        return _fail(result)
    return ActionResult(ok=True, message=f"凭据已确认，当前阶段{result['stage']}",
                        entry=result)


@router.post("/{cred_id:int}/claim", response_model=ActionResult)
def claim(cred_id: int, payload: ClaimPayload) -> ActionResult:
    """登记领用：把指定范围从未领用划到已领用，只有已领用部分可发运。"""
    _require(cred_id)
    result = _service().claim_scope(cred_id, payload.scope)
    if "error" in result:
        return _fail(result)
    return ActionResult(
        ok=True,
        message=f"已领用 {result['claimed'] or '无'}，剩余未领用 {result['unclaimed'] or '无'}",
        entry=result,
    )


@router.post("/{cred_id:int}/handover", response_model=ActionResult)
def handover(cred_id: int, payload: HandoverPayload) -> ActionResult:
    """执行数据域交接：旧凭据按策略保留或要求重新确认，新来源可同时提交新窗口。"""
    _require(cred_id)
    result = _service().handover(
        cred_id,
        payload.new_source_code,
        policy=payload.policy,
        new_batch_code=payload.new_batch_code,
        new_window_code=payload.new_window_code,
        new_scope=payload.new_scope,
        operator=payload.operator,
    )
    if "error" in result:
        return _fail(result)
    policy_text = ("旧凭据要求发货前重新确认"
                   if result["requires_reconfirm"] else "旧凭据保留到结案")
    return ActionResult(
        ok=True,
        message=f"交接完成：{result['previous_source']} → {result['new_source']}，{policy_text}",
        entry=result,
    )


@router.get("/{cred_id:int}/verify-scope")
def verify_scope(cred_id: int) -> dict[str, Any]:
    """按配置快照核对交付范围，回答目标端实际能接收哪一份源数据。"""
    _require(cred_id)
    result = _service().verify_scope(cred_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{cred_id:int}/verify-landed")
def verify_landed(cred_id: int) -> dict[str, Any]:
    """核对实际落地记录与凭据阶段，找出行号缺失或超出凭据范围的记录。"""
    _require(cred_id)
    result = _service().verify_landed(cred_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{cred_id:int}/receipt")
def receipt(cred_id: int) -> dict[str, Any]:
    """输出目标端回执：阶段、来源范围、配置快照摘要和落地核对结论。"""
    _require(cred_id)
    result = _service().receipt_for(cred_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
