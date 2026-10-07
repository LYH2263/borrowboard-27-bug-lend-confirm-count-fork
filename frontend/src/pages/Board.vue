<template>
  <div class="split">
    <section class="pane">
      <h2>可借物</h2>
      <div v-if="error" class="err">{{ error }}</div>
      <div v-for="i in board.available" :key="i.id" class="item">
        <strong>{{ i.title }}</strong>
        <span v-if="i.preview_hold" class="chip chip-hold">预演占用</span>
        <div class="muted">物主 {{ i.owner || '—' }}</div>
        <input v-model="forms[i.id].borrower" placeholder="借用人" />
        <input v-model="forms[i.id].due_date" placeholder="应还日 YYYY-MM-DD" />
        <template v-if="tickets[i.id]">
          <div class="muted">
            预演票 #{{ tickets[i.id].ticket_id }} · 将占用「{{ tickets[i.id].title }}」 · 应还 {{ tickets[i.id].due_date }}
          </div>
          <button :disabled="busy" @click="confirm(i.id)">确认借出</button>
          <button :disabled="busy" @click="cancel(i.id)">取消</button>
        </template>
        <button v-else :disabled="busy" @click="preview(i.id)">预演</button>
      </div>
    </section>
    <section class="pane">
      <h2>在借 / 逾期</h2>
      <div v-for="l in [...board.overdue, ...board.active]" :key="l.id" class="item" :class="{ overdue: l.overdue }">
        <strong>{{ l.title }}</strong> → {{ l.borrower }}
        <div class="muted">应还 {{ l.due_date }} {{ l.overdue ? '· 逾期' : '' }}</div>
        <button @click="ret(l.id)">归还</button>
      </div>
    </section>
  </div>
</template>
<script setup>
import { inject, reactive, ref, watch } from 'vue'
import { api } from '../api'
const board = inject('board')
const reload = inject('reloadBoard')
const forms = reactive({})
const tickets = reactive({})
const error = ref('')
const busy = ref(false)
watch(board, (b) => {
  for (const i of (b.available || [])) {
    if (!forms[i.id]) forms[i.id] = { borrower: '邻居', due_date: '2026-12-31' }
  }
}, { immediate: true, deep: true })
async function preview(id) {
  error.value = ''; busy.value = true
  try {
    tickets[id] = await api('/items/' + id + '/preview', { method: 'POST', body: JSON.stringify(forms[id]) })
  } catch (e) {
    error.value = '预演失败：' + e.message
  } finally {
    busy.value = false
  }
}
async function confirm(id) {
  error.value = ''; busy.value = true
  try {
    await api('/tickets/' + tickets[id].ticket_id + '/confirm', { method: 'POST', body: '{}' })
  } catch (e) {
    error.value = '确认失败：' + e.message
  } finally {
    delete tickets[id]
    busy.value = false
    // 成功或失败都以服务端为准重拉；失败的确认零副作用，可借数与预演前一致
    await reload()
  }
}
function cancel(id) {
  delete tickets[id]
}
async function ret(id) {
  await api('/loans/' + id + '/return', { method: 'POST', body: '{}' })
  await reload()
}
</script>
