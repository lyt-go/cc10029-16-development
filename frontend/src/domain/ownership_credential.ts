/** 批次归属凭据的前端领域逻辑：范围区间运算、阶段判断与回执解读。
 *
 * 后端返回的是“起止段”形式的交付范围，前端需要在核对面板上把它拆成
 * 已领用/未领用并高亮重叠，因此这里保留一份与后端等价的纯函数实现。
 */

export type Segment = [number, number]

export interface ScopeVerification {
  credential_code: string
  verified: boolean
  reasons: string[]
  stage: string
  source_code: string
  target_code: string
  domain: string
  scope: string
  claimed: string
  unclaimed: string
  scope_size: number
  claimed_size: number
  available_records: number | null
  snapshot_digest: string
  snapshot: Record<string, unknown>
}

export interface LandedVerification {
  credential_code: string
  batch_code: string
  stage: string
  stage_ok: boolean
  verified: boolean
  landed_count: number
  scope: string
  claimed: string
  expected_count: number
  missing_lines: number[]
  unowned_lines: number[]
  outside_claim_lines: number[]
  artifact_found: boolean
}

export interface CredentialReceipt {
  receipt_code: string
  credential_code: string
  source_code: string
  target_code: string
  domain: string
  batch_code: string
  stage: string
  handover_policy: string
  scope_verified: boolean
  scope_reasons: string[]
  landed_verified: boolean
  landed_count: number
  snapshot_digest: string
  history_immutable: boolean
}

export interface DeliveryResolution {
  target_code: string
  requested: string
  conflicts: { target_no: number; candidates: { credential_code: string; source_code: string }[] }[]
  conflict_count: number
  unowned: number[]
  unowned_count: number
  by_source: Record<string, string>
  distinct_sources: string[]
}

export function normalize(segments: Segment[]): Segment[] {
  const ordered = segments
    .map(([a, b]) => [Math.min(a, b), Math.max(a, b)] as Segment)
    .filter(([a]) => a >= 1)
    .sort((x, y) => x[0] - y[0])
  const merged: Segment[] = []
  for (const [start, end] of ordered) {
    const last = merged[merged.length - 1]
    if (last && start <= last[1] + 1) last[1] = Math.max(last[1], end)
    else merged.push([start, end])
  }
  return merged
}

export function parseScope(text: string): Segment[] {
  if (!text.trim()) return []
  const segments: Segment[] = []
  for (const part of text.split(/[,，;；、\s]+/).filter(Boolean)) {
    const token = part.replace(/[~—–]/g, '-')
    if (token.includes('-')) {
      const [left, right] = token.split('-', 2).map((value) => Number(value))
      segments.push([Math.min(left, right), Math.max(left, right)])
    } else {
      const value = Number(token)
      segments.push([value, value])
    }
  }
  return normalize(segments)
}

export function formatScope(segments: Segment[]): string {
  return normalize(segments)
    .map(([start, end]) => (start === end ? String(start) : `${start}-${end}`))
    .join(',')
}

export function scopeSize(segments: Segment[]): number {
  return normalize(segments).reduce((total, [start, end]) => total + end - start + 1, 0)
}

export function subtractScope(base: Segment[], removed: Segment[]): Segment[] {
  let remaining = normalize(base)
  for (const [cutStart, cutEnd] of normalize(removed)) {
    const next: Segment[] = []
    for (const [start, end] of remaining) {
      if (cutEnd < start || cutStart > end) {
        next.push([start, end])
        continue
      }
      if (start < cutStart) next.push([start, cutStart - 1])
      if (end > cutEnd) next.push([cutEnd + 1, end])
    }
    remaining = next
  }
  return normalize(remaining)
}

/** 把交付范围拆成三段：已领用、未领用、以及两者之外的空缺。 */
export function splitScope(
  scope: string,
  claimed: string,
  unclaimed: string,
): { claimedSegment: Segment[]; unclaimedSegment: Segment[]; gap: Segment[] } {
  const full = parseScope(scope)
  const claimedSegment = parseScope(claimed)
  const unclaimedSegment = parseScope(unclaimed)
  const accounted = normalize([...claimedSegment, ...unclaimedSegment])
  return { claimedSegment, unclaimedSegment, gap: subtractScope(full, accounted) }
}

const STAGE_TONE: Record<string, 'draft' | 'active' | 'warn' | 'done' | 'muted'> = {
  草稿: 'draft',
  待领用: 'active',
  已领用: 'active',
  待重新确认: 'warn',
  已落地: 'done',
  已结案: 'done',
  已撤销: 'muted',
}

export function stageTone(stage: string): 'draft' | 'active' | 'warn' | 'done' | 'muted' {
  return STAGE_TONE[stage] ?? 'draft'
}

/** 凭据能否继续发运：只有已领用且不在待重新确认阶段才可以。 */
export function canShip(stage: string, claimedSize: number): boolean {
  return claimedSize > 0 && !['待重新确认', '已撤销', '草稿'].includes(stage)
}

export interface ReceiptSummary {
  ok: boolean
  headline: string
  details: { label: string; value: string }[]
}

/** 把回执翻成操作员能直接读懂的一段结论。 */
export function summarizeReceipt(receipt: CredentialReceipt): ReceiptSummary {
  const details = [
    { label: '凭据编号', value: receipt.credential_code },
    { label: '回执编号', value: receipt.receipt_code },
    { label: '来源 → 目标', value: `${receipt.source_code} → ${receipt.target_code}` },
    { label: '数据域', value: receipt.domain || '—' },
    { label: '批次', value: receipt.batch_code || '—' },
    { label: '当前阶段', value: receipt.stage },
    { label: '交接策略', value: receipt.handover_policy },
    { label: '已落地记录', value: String(receipt.landed_count) },
    { label: '快照摘要', value: receipt.snapshot_digest || '—' },
  ]
  if (receipt.history_immutable) {
    return { ok: true, headline: '历史凭据已结案：交付范围与落地情况保持原样，可按批次回查。', details }
  }
  if (!receipt.scope_verified) {
    return {
      ok: false,
      headline: `交付范围与配置快照不一致：${receipt.scope_reasons.join('；') || '原因未知'}`,
      details,
    }
  }
  if (!receipt.landed_verified) {
    return { ok: false, headline: '落地记录尚未与凭据阶段对齐，需要核对实际接收情况。', details }
  }
  return { ok: true, headline: '交付范围与落地记录均与凭据一致。', details }
}
