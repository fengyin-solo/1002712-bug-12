<template>
  <section class="page" data-module="quaycrane">
    <header class="page-head">
      <div>
        <h2>岸桥调度管理</h2>
        <p class="page-desc">效率只有一份记录：分配、补数、释放都按同一份流水统一重算；面板与详情同源，刷新后仍是最新值。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记岸桥</button>
        <button class="btn" type="button" @click="exportRows">导出岸桥调度清单</button>
        <button class="btn ghost" type="button" @click="recompute">按新算法重算全部效率</button>
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
          <th>版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '作业效率'" class="link" type="button" @click="openDetail(row)">
              {{ formatEfficiency(row[column]) }}
            </button>
            <template v-else>{{ column === '作业船舶' || column === '操作司机' ? (row[column] || '—') : (row[column] ?? '—') }}</template>
          </td>
          <td>v{{ row.version ?? 0 }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button class="link" type="button" @click="openAction('分配作业', row)" :disabled="row.status === '作业中'">分配作业</button>
            <button class="link" type="button" @click="openAction('提交作业量', row)" :disabled="row.status !== '作业中'">补数/换司机</button>
            <button class="link" type="button" @click="openAction('释放岸桥', row)" :disabled="row.status !== '作业中'">释放岸桥</button>
            <button class="link" type="button" @click="openAction('登记检修', row)" :disabled="row.status === '检修中'">登记检修</button>
            <button class="link" type="button" @click="openAction('完成检修', row)" :disabled="row.status !== '检修中'">完成检修</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无岸桥调度数据，可先登记岸桥</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条岸桥调度记录，效率算法 {{ algorithmVersion }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <!-- 详情：进入时实时拉取单台岸桥，保证看到的是刚落库的新值 -->
    <div v-if="detailVisible" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ detail?.['岸桥编号'] }} 作业效率详情</h3>
          <button class="link" type="button" @click="closeDetail">关闭</button>
        </div>
        <div v-if="detail" class="modal-body">
          <div class="detail-grid">
            <span>岸桥型号</span><strong>{{ detail['岸桥型号'] }}</strong>
            <span>当前状态</span><strong>{{ detail['岸桥状态'] }}</strong>
            <span>作业船舶</span><strong>{{ detail['作业船舶'] || '—' }}</strong>
            <span>操作司机</span><strong>{{ detail['操作司机'] || '—' }}</strong>
            <span>作业效率</span><strong>{{ formatEfficiency(detail['作业效率']) }}</strong>
            <span>数据版本</span><strong>v{{ detail.version }}</strong>
            <span>流水提交</span><strong>{{ detail.record_version ?? 0 }} 次</strong>
            <span>算法版本</span><strong>v{{ detail.efficiency_version }}</strong>
            <span>占用开始</span><strong>{{ detail['占用开始时间'] || '—' }}</strong>
            <span>最近提交</span><strong>{{ detail['最近提交时间'] || '—' }}</strong>
          </div>

          <h4>各档位累计（额定值校验口径）</h4>
          <table class="data-table inner-table">
            <thead>
              <tr><th>档位</th><th>累计箱量</th><th>累计分钟</th><th>档位效率</th><th>额定上限</th></tr>
            </thead>
            <tbody>
              <tr v-for="tier in ratedTiers(detail)" :key="tier">
                <td>{{ tier }}</td>
                <td>{{ detail.segments?.[tier]?.moves ?? 0 }}</td>
                <td>{{ detail.segments?.[tier]?.minutes ?? 0 }}</td>
                <td>{{ tierRate(detail, tier) }}</td>
                <td>{{ detail['额定效率']?.[tier] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>

          <h4>提交流水（同一份记录）</h4>
          <ul v-if="detail.history?.length" class="history-list">
            <li v-for="(item, index) in detail.history" :key="index">
              {{ item.at }} ｜ {{ item.action }} ｜ 司机：{{ item.driver || '未变更' }} ｜ 本次效率：{{ item.efficiency }}
            </li>
          </ul>
          <p v-else class="page-desc">暂无提交流水。</p>
        </div>
      </div>
    </div>

    <!-- 分配 / 补数 / 释放 / 检修 -->
    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ formAction }} · {{ formRow?.['岸桥编号'] }}</h3>
          <button class="link" type="button" @click="closeForm">取消</button>
        </div>
        <form class="modal-body" @submit.prevent="submitForm">
          <p class="page-desc">基于记录版本 v{{ formVersion }} 提交；若期间已被先落库的提交顶过，本次将被拒绝，不会覆盖原值。</p>
          <label v-if="needAssignment" class="form-item">
            <span>作业船舶 *</span>
            <input v-model="form.vessel" required placeholder="如：中远海运-双鱼座" />
          </label>
          <label class="form-item">
            <span>操作司机{{ needAssignment ? ' *' : '（换司机时填写）'}}</span>
            <input v-model="form.driver" :required="needAssignment" placeholder="如：张建国" />
          </label>

          <template v-if="needSegments">
            <h4>本次作业分段（逐档位，超出额定值将整单拒绝）</h4>
            <table class="data-table inner-table">
              <thead>
                <tr><th>档位</th><th>本次箱量</th><th>本次分钟</th><th>额定效率上限</th></tr>
              </thead>
              <tbody>
                <tr v-for="tier in editableTiers" :key="tier">
                  <td>{{ tier }}</td>
                  <td><input v-model.number="form.segments[tier].moves" type="number" min="0" step="0.1" /></td>
                  <td><input v-model.number="form.segments[tier].minutes" type="number" min="0" step="0.1" /></td>
                  <td>{{ formRow?.['额定效率']?.[tier] }}</td>
                </tr>
              </tbody>
            </table>
          </template>

          <p v-if="formError" class="error-text">{{ formError }}</p>
          <div class="form-actions">
            <button class="btn" type="button" @click="closeForm">取消</button>
            <button class="btn primary" type="submit">确认{{ formAction }}</button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Segment = { moves: number | null; minutes: number | null }
type Row = Record<string, any>

const ENDPOINT = '/api/quaycrane'
const columns = ["岸桥编号", "岸桥型号", "额定起重量", "吊具类型", "作业船舶", "作业效率", "操作司机", "岸桥状态"]
const stats = ref([
  { label: "空闲岸桥", value: 0 },
  { label: "作业岸桥", value: 0 },
  { label: "检修岸桥", value: 0 },
  { label: "平均作业效率", value: '—' },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const algorithmVersion = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["岸桥编号", "岸桥型号", "额定起重量"]

const detailVisible = ref(false)
const detail = ref<Row | null>(null)

const formVisible = ref(false)
const formAction = ref('')
const formRow = ref<Row | null>(null)
const formVersion = ref(0)
const formError = ref('')
const form = reactive<{ vessel: string; driver: string; segments: Record<string, Segment> }>({
  vessel: '',
  driver: '',
  segments: {},
})

const needAssignment = computed(() => formAction.value === '分配作业')
const needSegments = computed(() => ['分配作业', '提交作业量', '释放岸桥'].includes(formAction.value))
const editableTiers = computed(() => Object.keys(formRow.value?.['额定效率'] ?? {}))

function formatEfficiency(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${value} 箱/时`
}

function ratedTiers(row: Row): string[] {
  return Object.keys(row['额定效率'] ?? {})
}

function tierRate(row: Row, tier: string): string {
  const seg = row.segments?.[tier]
  if (!seg || !seg.minutes) return '—'
  return `${((seg.moves / seg.minutes) * 60).toFixed(2)} 箱/时`
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

async function reload() {
  errorMessage.value = ''
  infoMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('岸桥列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value[0].value = rows.value.filter((row) => row.status === '空闲').length
    stats.value[1].value = rows.value.filter((row) => row.status === '作业中').length
    stats.value[2].value = rows.value.filter((row) => row.status === '检修中').length
    const withValue = rows.value.filter((row) => row['作业效率'] !== null && row['作业效率'] !== undefined)
    const average = withValue.length
      ? (withValue.reduce((sum, row) => sum + Number(row['作业效率']), 0) / withValue.length).toFixed(2)
      : '—'
    stats.value[3].value = average === '—' ? '—' : `${average} 箱/时`
    algorithmVersion.value = rows.value[0]?.efficiency_version ? `v${rows.value[0].efficiency_version}` : ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '岸桥调度列表读取失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  // 每次进详情都重新请求，避免拿着上一班的旧值
  const response = await request(`${ENDPOINT}/${row.id}`)
  if (!response.ok) {
    errorMessage.value = '岸桥详情读取失败，请刷新列表'
    return
  }
  detail.value = await response.json()
  detailVisible.value = true
}

function closeDetail() {
  detailVisible.value = false
  detail.value = null
}

async function openAction(action: string, row: Row) {
  errorMessage.value = ''
  formError.value = ''
  // 打开弹窗前先拉最新详情，版本号以服务端为准
  const response = await request(`${ENDPOINT}/${row.id}`)
  if (!response.ok) {
    errorMessage.value = '岸桥最新状态读取失败，请刷新列表后再操作'
    return
  }
  const latest: Row = await response.json()
  formAction.value = action
  formRow.value = latest
  formVersion.value = Number(latest.version ?? 0)
  form.vessel = latest['作业船舶'] ?? ''
  form.driver = action === '提交作业量' ? '' : (latest['操作司机'] ?? '')
  form.segments = {}
  for (const tier of Object.keys(latest['额定效率'] ?? {})) {
    form.segments[tier] = { moves: null, minutes: null }
  }
  formVisible.value = true
}

function closeForm() {
  formVisible.value = false
  formRow.value = null
  formError.value = ''
}

function buildSegments(): Record<string, { moves: number; minutes: number }> {
  const segments: Record<string, { moves: number; minutes: number }> = {}
  for (const tier of editableTiers.value) {
    const moves = Number(form.segments[tier]?.moves ?? 0)
    const minutes = Number(form.segments[tier]?.minutes ?? 0)
    if (moves === 0 && minutes === 0) continue
    segments[tier] = { moves, minutes }
  }
  return segments
}

async function submitForm() {
  formError.value = ''
  const segments = needSegments.value ? buildSegments() : {}
  try {
    const response = await request(`${ENDPOINT}/${formRow.value?.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        action: formAction.value,
        version: formVersion.value,
        vessel: form.vessel,
        driver: form.driver,
        segments,
      }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      // 409 占用/版本冲突、422 越界，原因都由后端点明
      formError.value = payload.detail || '操作未生效，请稍后重试'
      if (response.status === 409) {
        // 记录已被顶过，关掉旧表单，强制按最新值重开
        const row = formRow.value
        closeForm()
        await reload()
        if (row) await openAction(formAction.value, row)
        formError.value = payload.detail || '该岸桥已被先落库的提交占用，请基于最新记录操作'
      }
      return
    }
    closeForm()
    infoMessage.value = payload.message || '已按统一记录重算效率'
    await reload()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '岸桥调度操作失败'
  }
}

async function recompute() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/recalculate-efficiency`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '效率重算失败')
    }
    infoMessage.value = `已按新算法重算 ${payload.updated} 条效率记录（${payload.algorithm_version}）`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '效率重算失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal {
  background: #fff; border-radius: 10px; width: min(760px, 92vw);
  max-height: 86vh; overflow: auto; padding: 18px 20px;
}
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 16px; }
.modal-body h4 { margin: 14px 0 6px; font-size: 13px; }
.detail-grid {
  display: grid; grid-template-columns: 90px 1fr 90px 1fr; gap: 6px 12px;
  font-size: 13px; margin: 10px 0;
}
.detail-grid span { color: var(--muted); }
.inner-table { margin: 6px 0; }
.inner-table input { width: 100px; padding: 4px 6px; }
.history-list { margin: 6px 0; padding-left: 18px; font-size: 12px; color: var(--muted); }
.history-list li { margin-bottom: 4px; }
.form-item { display: block; margin: 10px 0; font-size: 13px; }
.form-item span { display: block; color: var(--muted); margin-bottom: 4px; }
.form-item input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.info-text { color: #17693a; }
.link:disabled { color: #9aa7b5; cursor: not-allowed; }
</style>
