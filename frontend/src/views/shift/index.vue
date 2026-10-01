<template>
  <section class="page" data-module="shift">
    <header class="page-head">
      <div>
        <h2>工班管理管理</h2>
        <p class="page-desc">维护工班，围绕工班编号、工班名称、当班组长、作业线数做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记工班</button>
        <button class="btn" type="button" @click="exportRows">导出工班管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <!-- 交班时核对岸桥效率：数据与调度面板同一份台账，不再出现两班两个数 -->
    <section class="crane-efficiency">
      <header class="section-head">
        <h3>岸桥效率交班对照</h3>
        <span class="section-hint">与调度面板同源（岸桥效率台账）· 共 {{ craneRows.length }} 台</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>岸桥编号</th>
            <th>作业效率（次/小时）</th>
            <th>效率档位</th>
            <th>操作司机</th>
            <th>作业船舶</th>
            <th>台账版本</th>
            <th>最后更新时间（UTC）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in craneRows" :key="item.岸桥编号">
            <td>{{ item.岸桥编号 }}</td>
            <td>{{ item.作业效率 ?? '—' }}</td>
            <td>{{ item.效率档位 ?? '—' }}</td>
            <td>{{ item.操作司机 || '—' }}</td>
            <td>{{ item.作业船舶 || '—' }}</td>
            <td>v{{ item.version }}</td>
            <td>{{ item.updated_at ? item.updated_at.replace('T', ' ').slice(0, 19) : '—' }}</td>
          </tr>
          <tr v-if="!craneRows.length">
            <td colspan="7" class="empty-state">暂无岸桥效率台账数据</td>
          </tr>
        </tbody>
      </table>
    </section>

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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
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
          <td :colspan="columns.length + 1" class="empty-state">暂无工班管理数据，可先登记工班</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条工班管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type CraneEfficiencyRow = {
  岸桥编号: string
  作业效率: number | null
  效率档位: string | null
  操作司机: string
  作业船舶: string
  version: number
  updated_at: string | null
}

const ENDPOINT = '/api/shift'
const columns = ["工班编号", "工班名称", "当班组长", "作业线数", "出勤人数", "作业时段", "作业效率", "工班状态"]
const actions = ["开始当班", "完成交班", "临时调配"]
const statuses = ["待交班", "当班中", "已交班", "已休班"]
const stats = [{"label": "当班工班数", "value": 0}, {"label": "出勤总人数", "value": 0}, {"label": "完成作业线", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
// 岸桥效率交班对照：直接读岸桥效率台账，保证与调度面板同源
const craneRows = ref<CraneEfficiencyRow[]>([])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '工班登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('工班管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '工班管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('工班列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '工班管理列表读取失败'
  }
}

async function loadCraneEfficiency() {
  // 与调度面板同一个接口、同一份台账，交班核对不再有两个数
  try {
    const response = await request('/api/quaycrane/efficiency')
    if (!response.ok) return
    const payload = await response.json()
    craneRows.value = payload.items ?? []
  } catch {
    craneRows.value = []
  }
}

onMounted(() => {
  void reload()
  void loadCraneEfficiency()
})
</script>

<style scoped>
.crane-efficiency {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
}
.section-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}
.section-head h3 { margin: 0; font-size: 14px; }
.section-hint { color: var(--muted); font-size: 12px; }
</style>
