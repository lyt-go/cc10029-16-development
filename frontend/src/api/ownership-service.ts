/** 批次归属凭据接口封装：签发、领用、交接、核对与历史回查。 */
import { request } from './client'
import type { ActionResult } from '@/data/types'
import type {
  CredentialReceipt,
  DeliveryResolution,
  LandedVerification,
  ScopeVerification,
} from '@/domain/ownership_credential'

export interface IssueCredentialInput {
  source_code: string
  target_code: string
  batch_code?: string
  window_code?: string
  scope?: string | null
  domain?: string
  handover_policy?: string
}

export interface HandoverInput {
  new_source_code: string
  policy?: string
  new_batch_code?: string
  new_window_code?: string
  new_scope?: string | null
  operator?: string
}

export interface CredentialHistoryItem {
  credential_code: string
  source_code: string
  target_code: string
  domain: string
  batch_code: string
  scope: string
  claimed: string
  stage: string
  handover_policy: string
  receipt_code: string
  immutable: boolean
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail === 'string' ? payload.detail : '归属凭据接口返回异常',
    )
  }
  return payload as T
}

export async function issueCredential(input: IssueCredentialInput): Promise<ActionResult> {
  const response = await request('/api/ownership_credential/issue', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return await readJson<ActionResult>(response)
}

export async function confirmCredential(id: number): Promise<ActionResult> {
  const response = await request(`/api/ownership_credential/${id}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ credential_code: '' }),
  })
  return await readJson<ActionResult>(response)
}

export async function claimScope(id: number, scope: string): Promise<ActionResult> {
  const response = await request(`/api/ownership_credential/${id}/claim`, {
    method: 'POST',
    body: JSON.stringify({ scope }),
  })
  return await readJson<ActionResult>(response)
}

export async function handoverCredential(id: number, input: HandoverInput): Promise<ActionResult> {
  const response = await request(`/api/ownership_credential/${id}/handover`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return await readJson<ActionResult>(response)
}

export async function verifyScope(id: number): Promise<ScopeVerification> {
  return await readJson<ScopeVerification>(await request(`/api/ownership_credential/${id}/verify-scope`))
}

export async function verifyLanded(id: number): Promise<LandedVerification> {
  return await readJson<LandedVerification>(await request(`/api/ownership_credential/${id}/verify-landed`))
}

export async function loadReceipt(id: number): Promise<CredentialReceipt> {
  return await readJson<CredentialReceipt>(await request(`/api/ownership_credential/${id}/receipt`))
}

export async function resolveDelivery(
  targetCode: string,
  recordRange: string,
): Promise<DeliveryResolution> {
  const query = new URLSearchParams({ target_code: targetCode, record_range: recordRange })
  return await readJson<DeliveryResolution>(await request(`/api/ownership_credential/resolve?${query}`))
}

export async function loadHistory(
  params: { source_code?: string; domain?: string; target_code?: string; include_active?: boolean } = {},
): Promise<{ items: CredentialHistoryItem[]; total: number; scope: string }> {
  const query = new URLSearchParams()
  if (params.source_code) query.set('source_code', params.source_code)
  if (params.domain) query.set('domain', params.domain)
  if (params.target_code) query.set('target_code', params.target_code)
  if (params.include_active) query.set('include_active', 'true')
  return await readJson(await request(`/api/ownership_credential/history?${query}`))
}
