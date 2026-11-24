<template>
  <section class="page" data-module="ownership_credential">
    <header class="page-head">
      <div>
        <h2>批次归属凭据</h2>
        <p class="page-desc">
          登记批次数据的归属凭据，按来源、交付范围与领用情况核对目标端实际接收的源数据。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-issue" type="button" @click="openIssue">
          签发凭据
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="credential-layout">
      <div>
        <table class="data-table">
          <thead>
            <tr>
              <th>凭据编号</th>
              <th>来源 → 目标</th>
              <th>数据域</th>
              <th>交付范围（已领用 / 未领用）</th>
              <th>起始序号</th>
              <th>阶段</th>
              <th>策略</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="String(row.id)"
              :data-testid="`cred-row-${row.id}`"
              :class="{ selected: selected?.id === row.id }"
              @click="select(row)"
            >
              <td>{{ row['凭据编号'] }}</td>
              <td>{{ row['源端编号'] }} → {{ row['目标编号'] }}</td>
              <td>{{ row['数据域'] || '—' }}</td>
              <td>
                <div class="scope-bar" :title="`交付范围 ${row['交付范围']}`">
                  <span
                    class="scope-seg claimed"
                    :style="segmentStyle(row, 'claimed')"
                  ></span>
                  <span
                    class="scope-seg unclaimed"
                    :style="segmentStyle(row, 'unclaimed')"
                  ></span>
                </div>
                <span class="hint">
                  {{ row['已领用范围'] || '无' }} / {{ row['未领用范围'] || '无' }}
                </span>
              </td>
              <td>{{ row['起始序号'] }}</td>
              <td>
                <span class="badge" :class="stageTone(String(row.status))">{{ row.status }}</span>
              </td>
              <td>{{ row['交接策略'] || '—' }}</td>
              <td class="row-actions" @click.stop>
                <button
                  v-for="action in actionsFor(row)"
                  :key="action"
                  class="link"
                  type="button"
                  :data-testid="`cred-action-${row.id}-${action}`"
                  @click="runRowAction(action, row)"
                >
                  {{ action }}
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="8" class="empty-state">暂无批次归属凭据，可先签发一份。</td>
            </tr>
          </tbody>
        </table>

        <div class="panel" style="margin-top: 12px">
          <h3>目标端归属核对</h3>
          <p class="hint">
            按目标端接收序号核对实际接收的是哪一份源数据；两个来源同时覆盖同一序号时标记为重叠。
          </p>
          <form class="filter-bar" @submit.prevent="runResolve">
            <label class="filter-item">
              <span>目标编号</span>
              <input v-model="resolveForm.target_code" data-testid="resolve-target" placeholder="TARG-0003" />
            </label>
            <label class="filter-item">
              <span>接收序号范围</span>
              <input v-model="resolveForm.record_range" data-testid="resolve-range" placeholder="1-160" />
            </label>
            <button class="btn" data-testid="resolve-run" type="submit">核对归属</button>
          </form>
          <div v-if="resolution" data-testid="resolve-result">
            <p>
              覆盖来源：<strong>{{ resolution.distinct_sources.join('、') || '无' }}</strong>；
              重叠冲突 <span :class="{ 'conflict-tag': resolution.conflict_count > 0 }">
                {{ resolution.conflict_count }}
              </span> 条；无归属 {{ resolution.unowned_count }} 条。
            </p>
            <p v-for="(scopeText, source) in resolution.by_source" :key="source" class="hint">
              {{ source }} 覆盖目标序号 {{ scopeText }}
            </p>
            <p v-if="resolution.conflict_count" class="conflict-tag" data-testid="resolve-conflict">
              存在来源重叠，目标端需按回执确认应归属哪一份源数据。
            </p>
          </div>
        </div>
      </div>

      <aside>
        <div class="panel" data-testid="credential-detail">
          <h3>凭据详情</h3>
          <p v-if="!selected" class="hint">点击左侧凭据查看配置快照、交付范围与回执。</p>
          <template v-else>
            <dl class="panel-fields">
              <template v-for="(value, label) in detailFields" :key="label">
                <dt>{{ label }}</dt>
                <dd>{{ value || '—' }}</dd>
              </template>
            </dl>

            <h3 style="margin-top: 12px">交付范围拆分</h3>
            <div class="scope-bar">
              <span class="scope-seg claimed" :style="{ width: `${scopeWidths.claimed}%` }"></span>
              <span class="scope-seg unclaimed" :style="{ width: `${scopeWidths.unclaimed}%` }"></span>
              <span class="scope-seg gap" :style="{ width: `${scopeWidths.gap}%` }"></span>
            </div>
            <p class="hint">
              <span class="legend-dot claimed"></span>已领用 {{ split.claimedSegment.length ? formatScope(split.claimedSegment) : '无' }}
              <span class="legend-dot unclaimed" style="margin-left: 8px"></span>未领用 {{ formatScope(split.unclaimedSegment) || '无' }}
              <span v-if="split.gap.length" class="legend-dot gap" style="margin-left: 8px"></span>
              <span v-if="split.gap.length">空缺 {{ formatScope(split.gap) }}</span>
            </p>

            <button class="btn" data-testid="refresh-detail" type="button" @click="loadDetail(Number(selected.id))">
              刷新核对
            </button>
            <button
              class="btn"
              data-testid="open-handover"
              type="button"
              @click="openHandover(selected)"
            >
              归属交接
            </button>

            <div
              v-if="receiptSummary"
              class="callout"
              :class="receiptSummary.ok ? 'ok' : 'bad'"
              data-testid="receipt-summary"
            >
              {{ receiptSummary.headline }}
            </div>

            <div v-if="landed" class="panel" style="margin-top: 10px" data-testid="landed-check">
              <h3>落地核对</h3>
              <p class="hint">
                落地 {{ landed.landed_count }} 条，应核 {{ landed.expected_count }} 条；
                缺失 {{ landed.missing_lines.length }} 条，越界 {{ landed.unowned_lines.length }} 条。
              </p>
              <p v-if="landed.unowned_lines.length" class="conflict-tag">
                越界序号：{{ landed.unowned_lines.join(', ') }}
              </p>
            </div>
          </template>
        </div>
      </aside>
    </div>

    <div v-if="modal === 'issue'" class="modal-backdrop" data-testid="issue-modal">
      <form class="modal" @submit.prevent="submitIssue">
        <h3>签发批次归属凭据</h3>
        <label class="create-field">
          <span>源端编号</span>
          <input v-model="issueForm.source_code" data-testid="issue-source" required />
        </label>
        <label class="create-field">
          <span>目标编号</span>
          <input v-model="issueForm.target_code" data-testid="issue-target" required />
        </label>
        <label class="create-field">
          <span>批次编号（可空）</span>
          <input v-model="issueForm.batch_code" data-testid="issue-batch" />
        </label>
        <label class="create-field">
          <span>交付范围（留空取快照全量）</span>
          <input v-model="issueForm.scope" data-testid="issue-scope" placeholder="1-120" />
        </label>
        <label class="create-field">
          <span>数据域</span>
          <input v-model="issueForm.domain" data-testid="issue-domain" placeholder="订单域" />
        </label>
        <label class="create-field">
          <span>交接策略</span>
          <select v-model="issueForm.handover_policy" data-testid="issue-policy">
            <option value="保留到结案">保留到结案</option>
            <option value="发货前重新确认">发货前重新确认</option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="modal = null">取消</button>
          <button class="btn primary" data-testid="issue-submit" type="submit" :disabled="busy">
            {{ busy ? '提交中…' : '确认签发' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="modal === 'claim'" class="modal-backdrop" data-testid="claim-modal">
      <form class="modal" @submit.prevent="submitClaim">
        <h3>登记领用</h3>
        <p class="hint">从未领用范围划出一段作为已领用，只有已领用部分可以发运。</p>
        <label class="create-field">
          <span>领用范围</span>
          <input v-model="claimScope" data-testid="claim-scope" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="modal = null">取消</button>
          <button class="btn primary" data-testid="claim-submit" type="submit" :disabled="busy">
            {{ busy ? '提交中…' : '确认领用' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="modal === 'handover'" class="modal-backdrop" data-testid="handover-modal">
      <form class="modal" @submit.prevent="submitHandover">
        <h3>执行归属交接</h3>
        <p class="hint">
          交接只追加留痕，不撤销旧凭据，也不改写已落地的旧批。选择策略决定旧凭据是保留到结案，
          还是要求在发货前重新确认。
        </p>
        <label class="create-field">
          <span>新源端编号</span>
          <input v-model="handoverForm.new_source_code" data-testid="handover-source" required />
        </label>
        <label class="create-field">
          <span>交接策略</span>
          <select v-model="handoverForm.policy" data-testid="handover-policy">
            <option value="保留到结案">保留到结案</option>
            <option value="发货前重新确认">发货前重新确认</option>
          </select>
        </label>
        <label class="create-field">
          <span>新批次编号（可空）</span>
          <input v-model="handoverForm.new_batch_code" data-testid="handover-batch" />
        </label>
        <label class="create-field">
          <span>新交付范围（留空不签发新凭据）</span>
          <input v-model="handoverForm.new_scope" data-testid="handover-scope" />
        </label>
        <label class="create-field">
          <span>操作员</span>
          <input v-model="handoverForm.operator" data-testid="handover-operator" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="modal = null">取消</button>
          <button class="btn primary" data-testid="handover-submit" type="submit" :disabled="busy">
            {{ busy ? '提交中…' : '确认交接' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ rows.length }} 份批次归属凭据</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { listEntries, runAction } from '@/api/backend-service'
import {
  claimScope as apiClaimScope,
  confirmCredential,
  handoverCredential,
  issueCredential,
  loadReceipt,
  resolveDelivery,
  verifyLanded,
  verifyScope,
} from '@/api/ownership-service'
import type {
  CredentialReceipt,
  DeliveryResolution,
  LandedVerification,
  ScopeVerification,
  Segment,
} from '@/domain/ownership_credential'
import {
  formatScope,
  parseScope,
  scopeSize,
  splitScope,
  stageTone,
  summarizeReceipt,
} from '@/domain/ownership_credential'
import type { EntryRow } from '@/data/types'

type ModalKind = 'issue' | 'claim' | 'handover' | null

const rows = ref<EntryRow[]>([])
const selected = ref<EntryRow | null>(null)
const scopeVerification = ref<ScopeVerification | null>(null)
const landed = ref<LandedVerification | null>(null)
const receipt = ref<CredentialReceipt | null>(null)
const resolution = ref<DeliveryResolution | null>(null)
const errorMessage = ref('')
const modal = ref<ModalKind>(null)
const busy = ref(false)
const claimScope = ref('')

const issueForm = ref({
  source_code: '',
  target_code: '',
  batch_code: '',
  scope: '',
  domain: '',
  handover_policy: '保留到结案',
})
const claimTarget = ref<EntryRow | null>(null)
const handoverTarget = ref<EntryRow | null>(null)
const handoverForm = ref({
  new_source_code: '',
  policy: '保留到结案',
  new_batch_code: '',
  new_scope: '',
  operator: '',
})
const resolveForm = ref({ target_code: '', record_range: '' })

const stats = computed(() => [
  { label: '凭据总数', value: rows.value.length },
  {
    label: '待领用/待确认',
    value: rows.value.filter((row) => ['待领用', '待重新确认'].includes(String(row.status))).length,
  },
  {
    label: '已落地/已结案',
    value: rows.value.filter((row) => ['已落地', '已结案'].includes(String(row.status))).length,
  },
])

const detailFields = computed(() => {
  const row = selected.value
  if (!row) return {}
  return {
    凭据编号: String(row['凭据编号'] ?? ''),
    来源: String(row['源端编号'] ?? ''),
    目标端: String(row['目标编号'] ?? ''),
    数据域: String(row['数据域'] ?? ''),
    批次: String(row['批次编号'] ?? ''),
    交付范围: String(row['交付范围'] ?? ''),
    已领用: String(row['已领用范围'] ?? ''),
    未领用: String(row['未领用范围'] ?? ''),
    起始序号: String(row['起始序号'] ?? ''),
    阶段: String(row.status ?? ''),
    交接策略: String(row['交接策略'] ?? ''),
    快照摘要: String(row['配置快照'] ?? ''),
    回执编号: String(row['回执编号'] ?? ''),
  }
})

const split = computed(() => {
  const row = selected.value
  if (!row) return { claimedSegment: [] as Segment[], unclaimedSegment: [] as Segment[], gap: [] as Segment[] }
  return splitScope(
    String(row['交付范围'] ?? ''),
    String(row['已领用范围'] ?? ''),
    String(row['未领用范围'] ?? ''),
  )
})

const scopeWidths = computed(() => {
  const total = scopeSize(parseScope(String(selected.value?.['交付范围'] ?? ''))) || 1
  const claimed = scopeSize(split.value.claimedSegment) || 0
  const unclaimed = scopeSize(split.value.unclaimedSegment) || 0
  const gap = scopeSize(split.value.gap) || 0
  return {
    claimed: (claimed / total) * 100,
    unclaimed: (unclaimed / total) * 100,
    gap: (gap / total) * 100,
  }
})

const receiptSummary = computed(() => (receipt.value ? summarizeReceipt(receipt.value) : null))

function segmentStyle(row: EntryRow, kind: 'claimed' | 'unclaimed') {
  const total = scopeSize(parseScope(String(row['交付范围'] ?? ''))) || 1
  const value =
    kind === 'claimed'
      ? scopeSize(parseScope(String(row['已领用范围'] ?? '')))
      : scopeSize(parseScope(String(row['未领用范围'] ?? '')))
  return { width: `${(value / total) * 100}%` }
}

function actionsFor(row: EntryRow): string[] {
  const stage = String(row.status)
  if (stage === '草稿') return ['提交确认', '撤销凭据']
  if (stage === '待领用') return ['登记领用', '撤销凭据']
  if (stage === '已领用') return ['确认落地', '撤销凭据', '要求重新确认']
  if (stage === '待重新确认') return ['确认发运']
  if (stage === '已落地') return ['确认结案']
  if (stage === '已撤销') return ['确认结案']
  return []
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries('ownership_credential', {})
    rows.value = payload.items
    if (selected.value) {
      const found = rows.value.find((row) => row.id === selected.value?.id)
      if (found) selected.value = found
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '凭据列表读取失败'
  }
}

async function select(row: EntryRow) {
  selected.value = row
  await loadDetail(Number(row.id))
}

async function loadDetail(id: number) {
  errorMessage.value = ''
  try {
    const [scope, landedResult, receiptResult] = await Promise.all([
      verifyScope(id),
      verifyLanded(id),
      loadReceipt(id),
    ])
    scopeVerification.value = scope
    landed.value = landedResult
    receipt.value = receiptResult
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '凭据核对失败'
  }
}

async function runRowAction(action: string, row: EntryRow) {
  if (action === '登记领用') {
    claimTarget.value = row
    claimScope.value = String(row['未领用范围'] ?? '')
    modal.value = 'claim'
    return
  }
  errorMessage.value = ''
  const id = Number(row.id)
  try {
    let result
    if (action === '提交确认') {
      result = await confirmCredential(id)
    } else {
      result = await runAction('ownership_credential', id, action)
    }
    if (!result.ok) throw new Error(result.message)
    await reload()
    if (selected.value?.id === id) await loadDetail(id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

function openIssue() {
  issueForm.value = {
    source_code: '',
    target_code: '',
    batch_code: '',
    scope: '',
    domain: '',
    handover_policy: '保留到结案',
  }
  errorMessage.value = ''
  modal.value = 'issue'
}

async function submitIssue() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await issueCredential({
      source_code: issueForm.value.source_code,
      target_code: issueForm.value.target_code,
      batch_code: issueForm.value.batch_code || undefined,
      scope: issueForm.value.scope || null,
      domain: issueForm.value.domain,
      handover_policy: issueForm.value.handover_policy,
    })
    if (!result.ok) throw new Error(result.message)
    modal.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '签发失败'
  } finally {
    busy.value = false
  }
}

async function submitClaim() {
  if (!claimTarget.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await apiClaimScope(Number(claimTarget.value.id), claimScope.value)
    if (!result.ok) throw new Error(result.message)
    modal.value = null
    await reload()
    await loadDetail(Number(claimTarget.value.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领用失败'
  } finally {
    busy.value = false
  }
}

function openHandover(row: EntryRow) {
  handoverTarget.value = row
  handoverForm.value = {
    new_source_code: '',
    policy: '保留到结案',
    new_batch_code: '',
    new_scope: '',
    operator: '',
  }
  modal.value = 'handover'
}

async function submitHandover() {
  if (!handoverTarget.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await handoverCredential(Number(handoverTarget.value.id), {
      new_source_code: handoverForm.value.new_source_code,
      policy: handoverForm.value.policy,
      new_batch_code: handoverForm.value.new_batch_code,
      new_scope: handoverForm.value.new_scope || null,
      operator: handoverForm.value.operator,
    })
    if (!result.ok) throw new Error(result.message)
    modal.value = null
    await reload()
    if (selected.value) await loadDetail(Number(selected.value.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交接失败'
  } finally {
    busy.value = false
  }
}

async function runResolve() {
  errorMessage.value = ''
  try {
    resolution.value = await resolveDelivery(
      resolveForm.value.target_code,
      resolveForm.value.record_range,
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归属核对失败'
  }
}

onMounted(async () => {
  await reload()
  if (rows.value.length) await select(rows.value[0])
})

defineExpose({ openHandover })
</script>
