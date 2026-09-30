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

    <h3 class="section-title">岸桥作业效率（与岸桥调度面板同源）</h3>
    <p class="page-desc">数据统一取自岸桥调度的效率记录 {{ craneSummary.algorithm_version }}，不另行计算或缓存。</p>
    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">在作业岸桥</span>
        <strong class="stat-value">{{ craneSummary.working }} / {{ craneSummary.total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">平均作业效率</span>
        <strong class="stat-value">{{ formatEfficiency(craneSummary.average_efficiency) }}</strong>
      </article>
    </div>
    <table class="data-table crane-table">
      <thead>
        <tr><th>岸桥编号</th><th>岸桥型号</th><th>作业船舶</th><th>操作司机</th><th>作业效率</th><th>岸桥状态</th><th>记录版本</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in craneSummary.items" :key="String(item.id)">
          <td>{{ item['岸桥编号'] }}</td>
          <td>{{ item['岸桥型号'] }}</td>
          <td>{{ item['作业船舶'] || '—' }}</td>
          <td>{{ item['操作司机'] || '—' }}</td>
          <td>{{ formatEfficiency(item['作业效率']) }}</td>
          <td>{{ item['岸桥状态'] }}</td>
          <td>v{{ item.version }}</td>
        </tr>
        <tr v-if="!craneSummary.items.length">
          <td colspan="7" class="empty-state">暂无岸桥效率数据</td>
        </tr>
      </tbody>
    </table>
    <p v-if="craneError" class="error-text">{{ craneError }}</p>

    <h3 class="section-title">工班记录</h3>
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
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type CraneItem = {
  id: number
  岸桥编号: string
  岸桥型号: string
  作业效率: number | null
  作业船舶: string
  操作司机: string
  岸桥状态: string
  version: number
}

const ENDPOINT = '/api/shift'
const columns = ["工班编号", "工班名称", "当组长", "作业线数", "出勤人数", "作业时段", "作业效率", "工班状态"]
const actions = ["开始当班", "完成交班", "临时调配"]
const statuses = ["待交班", "当班中", "已交班", "已休班"]
const stats = [{"label": "当班工班数", "value": 0}, {"label": "出勤总人数", "value": 0}, {"label": "完成作业线", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const craneError = ref('')
const craneSummary = reactive<{
  algorithm_version: string
  total: number
  working: number
  average_efficiency: number | null
  items: CraneItem[]
}>({
  algorithm_version: '',
  total: 0,
  working: 0,
  average_efficiency: null,
  items: [],
})

function formatEfficiency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value} 箱/时`
}

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

async function reloadCraneEfficiency() {
  // 不自己算：直接读岸桥调度提供的同源汇总口
  craneError.value = ''
  try {
    const response = await request('/api/quaycrane/efficiency-summary')
    if (!response.ok) {
      throw new Error('岸桥效率同源数据读取失败')
    }
    const payload = await response.json()
    craneSummary.algorithm_version = payload.algorithm_version ?? ''
    craneSummary.total = payload.total ?? 0
    craneSummary.working = payload.working ?? 0
    craneSummary.average_efficiency = payload.average_efficiency ?? null
    craneSummary.items = payload.items ?? []
  } catch (error) {
    craneError.value = error instanceof Error ? error.message : '岸桥效率同源数据读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadCraneEfficiency()
})
</script>

<style scoped>
.section-title { font-size: 15px; margin: 18px 0 4px; }
.crane-table { margin: 8px 0 4px; }
</style>
