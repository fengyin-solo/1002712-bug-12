<template>
  <section class="page" data-module="quaycrane">
    <header class="page-head">
      <div>
        <h2>岸桥调度管理</h2>
        <p class="page-desc">作业效率全平台只认一份台账：分配与释放都按同一份记录统一重算，面板、详情与交班页同源。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="recalculate">按新算法重算存量效率</button>
        <button class="btn primary" type="button" @click="openCreate">登记岸桥</button>
        <button class="btn" type="button" @click="exportRows">导出岸桥调度清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>台账版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row, column) }}</td>
          <td>v{{ row.version ?? '—' }}<span v-if="isStale(row)" class="stale-tag">待重算</span></td>
          <td class="row-actions">
            <button class="link" type="button" @click="openEfficiency(row)">效率修正</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无岸桥调度数据，可先登记岸桥</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条岸桥调度记录，效率档位额定值：{{ ratedText }}（次/小时）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 分配/释放/效率修正共用一份台账表单：提交的是同一台岸桥的同一份记录 -->
    <div v-if="dialog.open" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3>{{ dialog.title }}</h3>
        <p class="modal-desc">
          岸桥 {{ dialog.craneCode }} · 当前效率
          <strong>{{ dialog.record?.作业效率 ?? '—' }}</strong>
          次/小时（台账版本 v{{ dialog.record?.version ?? '—' }}）
        </p>
        <div class="form-grid">
          <label>
            <span>效率档位 *</span>
            <select v-model="form.gear">
              <option value="" disabled>请选择档位</option>
              <option v-for="gear in gears" :key="gear" :value="gear">{{ gear }}（额定 {{ ratedByGear[gear] }}）</option>
            </select>
          </label>
          <label>
            <span>作业箱量（次）*</span>
            <input v-model.number="form.moves" type="number" min="0" step="1" placeholder="本班累计完成箱量" />
          </label>
          <label>
            <span>作业分钟 *</span>
            <input v-model.number="form.minutes" type="number" min="1" step="1" placeholder="本班累计作业分钟" />
          </label>
          <label>
            <span>操作司机</span>
            <input v-model="form.driver" placeholder="交班司机姓名" />
          </label>
          <label>
            <span>作业船舶</span>
            <input v-model="form.vessel" placeholder="可选" />
          </label>
          <label class="preview-cell">
            <span>重算预览</span>
            <input :value="previewText" disabled />
          </label>
        </div>
        <p v-if="dialog.error" class="error-text">{{ dialog.error }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeDialog">取消</button>
          <button class="btn primary" type="button" :disabled="dialog.saving" @click="confirmDialog">
            {{ dialog.saving ? '提交中…' : '确认并重算效率' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type LedgerRecord = {
  岸桥编号: string
  作业效率: number | null
  效率档位: string | null
  作业箱量: number | null
  作业分钟: number | null
  操作司机: string
  作业船舶: string
  algorithm_version: number
  version: number
  updated_at: string | null
  updated_by: string | null
}

const ENDPOINT = '/api/quaycrane'
const columns = ["岸桥编号", "岸桥型号", "额定起重量", "吊具类型", "作业船舶", "作业效率", "效率档位", "操作司机", "岸桥状态"]
const actions = ["分配作业", "释放岸桥", "登记检修"]
const gears = ["轻载档", "标准档", "重载档"]
const ALGORITHM_VERSION = 2
const GEAR_FACTOR: Record<string, number> = { 轻载档: 1.05, 标准档: 1.0, 重载档: 0.92 }

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const ratedByGear = ref<Record<string, number>>({ 轻载档: 40, 标准档: 32, 重载档: 25 })

const stats = computed(() => [
  { label: "空闲岸桥", value: rows.value.filter(r => r["岸桥状态"] === "空闲").length },
  { label: "作业岸桥", value: rows.value.filter(r => r["岸桥状态"] === "作业中").length },
  { label: "检修岸桥", value: rows.value.filter(r => r["岸桥状态"] === "检修中").length },
])

const ratedText = computed(() => gears.map(g => `${g} ${ratedByGear.value[g]}`).join(' / '))

type DialogMode = 'action' | 'efficiency'
const dialog = reactive({
  open: false,
  saving: false,
  title: '',
  mode: 'efficiency' as DialogMode,
  action: '',
  entryId: 0,
  craneCode: '',
  record: null as LedgerRecord | null,
  error: '',
})
const form = reactive({ gear: '', moves: '' as number | '', minutes: '' as number | '', driver: '', vessel: '' })

const previewText = computed(() => {
  if (!form.gear || typeof form.moves !== 'number' || typeof form.minutes !== 'number' || form.minutes <= 0) {
    return '填完档位/箱量/分钟后预览'
  }
  const value = Math.round((form.moves * 60 / form.minutes) * GEAR_FACTOR[form.gear] * 10) / 10
  const rated = ratedByGear.value[form.gear]
  return `${value} 次/小时${value > rated ? `（超出${form.gear}额定 ${rated}，不落库）` : ''}`
})

function formatCell(row: Row, column: string) {
  if (column === '作业效率') return row[column] == null ? '—' : `${row[column]} 次/小时`
  return row[column] ?? '—'
}

function isStale(row: Row) {
  return Number(row.algorithm_version || 0) > 0 && Number(row.algorithm_version) < ALGORITHM_VERSION
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '岸桥登记入口尚未接入审批流'
}

async function fetchLedger(craneCode: string): Promise<LedgerRecord> {
  // 每次打开都拉最新台账，保证「退出再进、刷新后读到的仍是新的那个数」
  const response = await request(`${ENDPOINT}/efficiency/${encodeURIComponent(craneCode)}`)
  if (!response.ok) throw new Error('岸桥效率台账读取失败')
  return (await response.json()) as LedgerRecord
}

function fillForm(record: LedgerRecord) {
  form.gear = record.效率档位 ?? ''
  form.moves = record.作业箱量 ?? ''
  form.minutes = record.作业分钟 ?? ''
  form.driver = record.操作司机 ?? ''
  form.vessel = record.作业船舶 ?? ''
}

async function openEfficiency(row: Row) {
  errorMessage.value = ''
  const craneCode = String(row["岸桥编号"])
  try {
    const record = await fetchLedger(craneCode)
    Object.assign(dialog, {
      open: true, mode: 'efficiency', title: `修正岸桥 ${craneCode} 作业效率`,
      action: '', entryId: Number(row.id), craneCode, record, error: '',
    })
    fillForm(record)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '效率台账读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  // 登记检修不涉及效率台账，直接执行
  if (action === '登记检修') {
    await postAction(Number(row.id), { action })
    return
  }
  const craneCode = String(row["岸桥编号"])
  try {
    const record = await fetchLedger(craneCode)
    Object.assign(dialog, {
      open: true, mode: 'action', title: `${action}：${craneCode}`,
      action, entryId: Number(row.id), craneCode, record, error: '',
    })
    fillForm(record)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '效率台账读取失败'
  }
}

function buildPayload(extra: Record<string, unknown> = {}) {
  return {
    values: {
      ...extra,
      expected_version: dialog.record?.version,
      效率档位: form.gear,
      作业箱量: form.moves,
      作业分钟: form.minutes,
      操作司机: form.driver,
      作业船舶: form.vessel,
    },
  }
}

async function confirmDialog() {
  dialog.error = ''
  dialog.saving = true
  try {
    let ok = false
    let message = ''
    let conflict = false
    if (dialog.mode === 'action') {
      const result = await postAction(dialog.entryId, buildPayload({ action: dialog.action }), true)
      ok = result.ok
      message = result.message
      conflict = result.conflict
    } else {
      const response = await request(
        `${ENDPOINT}/efficiency/${encodeURIComponent(dialog.craneCode)}/submit`,
        { method: 'POST', body: JSON.stringify(buildPayload()) },
      )
      const body = await response.json().catch(() => ({}))
      ok = response.ok && body.ok
      message = response.ok ? body.message : body.detail ?? '效率提交失败'
      conflict = body.conflict === true
    }
    if (!ok) {
      // 晚到的提交：只提示已被占用；用最新记录回填版本，不覆盖先落库的值
      if (conflict) {
        dialog.record = await fetchLedger(dialog.craneCode)
        fillForm(dialog.record)
        dialog.error = `${message}（已刷新为台账最新值，请核对后再提交）`
      } else {
        dialog.error = message
      }
      return
    }
    dialog.open = false
    errorMessage.value = ''
    await reload()
  } catch (error) {
    dialog.error = error instanceof Error ? error.message : '提交失败'
  } finally {
    dialog.saving = false
  }
}

async function postAction(entryId: number, payload: Record<string, unknown>, raw = false) {
  const response = await request(`${ENDPOINT}/${entryId}/actions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => ({ ok: false, message: '岸桥调度动作未生效' }))
  if (!raw) {
    if (!response.ok || !body.ok) throw new Error(body.message ?? '岸桥调度动作未生效，请稍后重试')
    await reload()
  }
  return body as { ok: boolean; message: string; conflict: boolean }
}

async function recalculate() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/efficiency/recalculate`, { method: 'POST' })
    const body = await response.json()
    if (!response.ok) throw new Error(body.detail ?? '存量效率重算失败')
    errorMessage.value = ''
    await reload()
    window.alert(`存量效率已按新算法重算：重算 ${body.recalculated} 条，跳过 ${body.skipped} 条`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '存量效率重算失败'
  }
}

function closeDialog() {
  dialog.open = false
  dialog.error = ''
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('岸桥列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '岸桥调度列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.stale-tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 10px;
  background: #fef3c7;
  color: #92400e;
  font-size: 11px;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 560px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal h3 { margin: 0 0 6px; font-size: 16px; }
.modal-desc { margin: 0 0 12px; color: var(--muted); font-size: 13px; }
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 14px;
}
.form-grid label span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-grid input, .form-grid select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.preview-cell input { background: #f8fafc; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
</style>
