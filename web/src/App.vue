<script setup>
import {language, message, t} from './i18n.js'
import LanguagePicker from './LanguagePicker.vue'
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import Glyph from './Glyph.vue'
import Logo from './Logo.vue'
import Sheet from './Sheet.vue'
import ServiceCard from './ServiceCard.vue'
import Editor from './Editor.vue'
import {api,apiError,copy,timeText,serviceState,sameLinkCheck,sameYouTubeChannels} from './model.js'

const doc=ref(null),draft=ref(null),authenticated=ref(false),editUrl=ref('')
const data=computed(()=>draft.value || doc.value)
const pageTitle=computed(()=>data.value?.settings.title || t('Dashboard'))
const modal=ref(''), groupId=ref(''), itemId=ref('')
const group=computed(()=>data.value?.groups.find(g=>g.id===groupId.value))
const entry=computed(()=>modal.value==='group'?group.value:group.value?.items.find(i=>i.id===itemId.value))
const sources=ref([]),statuses=ref({}),infrastructure=ref({}), now=ref(Date.now())
const statusLanguage=ref(language.value)
const statusError=ref(''),loadError=ref(''),error=ref(''),sourceError=ref(''),conflict=ref(false)
const loading=ref(false),working=ref(false),polling=ref(false),preview=ref(false)
const query=ref(''),search=ref(null),password=ref(''),history=ref([]),announcement=ref('')
const selectedStatus=ref(null)
const drag=ref(null),hover=ref(null),theme=ref(document.documentElement.dataset.theme||'system')
const proposed=ref({}),filePicker=ref(null),restoreFile=ref(null)
const themeMedia=matchMedia('(prefers-color-scheme: dark)'),osDark=ref(themeMedia.matches)
const dark=computed(()=>theme.value==='dark' || (theme.value==='system' && osDark.value))
const editing=computed(()=>!!draft.value && !preview.value)
const dirty=computed(()=>draft.value && JSON.stringify(draft.value)!==JSON.stringify(doc.value))
const allGroups=computed(()=>data.value?.groups||[])
const visibleGroups=computed(()=>{
  const q=query.value.trim().toLocaleLowerCase(language.value)
  if (editing.value || (!q && !selectedStatus.value)) return allGroups.value
  return allGroups.value.map(g=>{
    const groupMatches=g.name.toLocaleLowerCase(language.value).includes(q)
    const items=g.items.filter(item=>{
      if (selectedStatus.value && itemState(item)!==selectedStatus.value) return false
      return groupMatches || [item.name,item.subtitle,item.tag,item.url].some(v=>v?.toLocaleLowerCase(language.value).includes(q))
    })
    return {...g,items}
  }).filter(g=>g.items.length)
})
const visibleCount=computed(()=>visibleGroups.value.reduce((n,g)=>n+g.items.length,0))
const hostNotice=computed(()=>{
  const entry=Object.entries(infrastructure.value)[0]
  if(!entry)return null
  const [source,host]=entry
  const state=statusError.value?'stale':host.state
  if(!['down','warning','stale','unknown'].includes(state))return null
  const title=t(state==='down'?'{source} is unreachable':state==='warning'?'{source} reports a problem':'Status of {source} is unclear',{source})
  return {...host,state,title,detail:statusLanguage.value===language.value?host.detail:''}
})
const summary=computed(()=>{
  const c={ok:0,warning:0,down:0,unknown:0,stale:0}
  for(const g of allGroups.value) for(const i of g.items) {
    const key=itemState(i)
    if(key) c[key]++
  }
  return [
    ['ok',t('reachable'),c.ok],['warning',t('with warnings'),c.warning],['down',t('down'),c.down],
    ['stale',t('stale'),c.stale],['unknown',t('unknown'),c.unknown]
  ].filter(([key,,n])=>n>0 || key===selectedStatus.value)
})
const modalTitle=computed(()=>({
  login:t('Unlock editing'),settings:t('Settings'),item:entry.value?t('Edit service'):t('Add service'),
  group:group.value?t('Edit category'):t('Add category'),move:t('Move service'),restore:t('Restore backup'),
  discard:t('Discard draft?'),conflict:t('Load newer version?')
})[modal.value]||'')
let interval, announcementTimer, statusRevision=0
function say(text){announcement.value=text;clearTimeout(announcementTimer);announcementTimer=setTimeout(()=>announcement.value='',4200)}
function applyDocument(value) {
  if(doc.value){
    statusRevision++
    const currentItems=new Map(doc.value.groups.flatMap(g=>g.items).map(item=>[item.id,item]))
    for(const group of value.groups)for(const item of group.items){
      const original=currentItems.get(item.id)
      if(!original || ((item.kind==='link' || original.kind==='link') &&
        (item.kind!==original.kind || !sameLinkCheck(item,original))))delete statuses.value[item.id]
    }
  }
  doc.value={revision:value.revision,settings:value.settings,groups:value.groups}
  authenticated.value=value.authenticated===true
  editUrl.value=value.edit_url || ''
}
async function load(){
  loading.value=true;loadError.value=''
  try{
    applyDocument(await api('dashboard'))
    const params=new URL(location.href).searchParams
    if(params.get('edit')==='1' && !editUrl.value)begin()
  }
  catch(e){loadError.value=apiError(e, 'Could not load the dashboard. {detail}')}
  finally{loading.value=false}
}
async function poll(){
  if(polling.value) return
  now.value=Date.now();polling.value=true
  const revisionAtRequest=statusRevision
  const requestedLanguage=language.value
  try{
    const result=await api('status')
    if(revisionAtRequest===statusRevision){
      statuses.value=result.items || {};infrastructure.value=result.infrastructure || {};statusError.value=''
      statusLanguage.value=requestedLanguage
    }
  }catch(e){if(revisionAtRequest===statusRevision)statusError.value=apiError(e, 'Could not retrieve status. {detail}')}
  finally{
    polling.value=false
    if(revisionAtRequest!==statusRevision)void poll()
  }
}
function live(item){
  if(draft.value){
    const original=doc.value.groups.flatMap(g=>g.items).find(i=>i.id===item.id)
    if(!original || original.kind!==item.kind || original.source!==item.source || (item.kind==='http' && original.url!==item.url))return null
    if(item.kind==='link' && !sameLinkCheck(original,item))return null
    if(item.kind==='youtube' && !sameYouTubeChannels(original.channels,item.channels))return null
  }
  const status=statuses.value[item.id]
  if(status && statusLanguage.value!==language.value)return {...status,detail:'',metrics:[]}
  return status
}
function itemState(item){return serviceState(item,live(item),!!statusError.value)}
function clearFilters(){query.value='';selectedStatus.value=null}
async function loadSources(){
  sourceError.value=''
  try{sources.value=(await api('sources')).sources||[]}
  catch(e){sourceError.value=apiError(e, 'Could not load data sources. {detail}')}
}
function show(name,g='',i=''){groupId.value=g;itemId.value=i;error.value='';modal.value=name}
function close(){
  if(working.value)return
  modal.value='';password.value='';error.value='';restoreFile.value=null
}
function begin(){
  if(editUrl.value){
    try{const target=new URL(editUrl.value);if(target.protocol==='https:'){target.searchParams.set('edit','1');target.searchParams.set('lang',language.value);location.assign(target.href);return}}catch{}
    say(message('The HTTPS address for editing is invalid.'));return
  }
  if(!authenticated.value){show('login');return}
  if(!draft.value){draft.value=copy(doc.value);history.value=[];clearFilters();conflict.value=false}
  preview.value=false;void loadSources()
}
async function login(){
  if(working.value)return
  working.value=true;error.value=''
  try{
    await api('login','POST',{password:password.value});authenticated.value=true;password.value=''
    modal.value='';begin()
  }catch(e){error.value=e.status===401?message('Incorrect password.'):e.status===429?message('Too many sign-in attempts. Try again later.'):apiError(e)}
  finally{working.value=false}
}
function change(fn,message){
  const prev=copy(draft.value)
  fn()
  if(JSON.stringify(prev)===JSON.stringify(draft.value)) return
  history.value=[...history.value.slice(-29),prev]
  if(message)say(message)
}
function undo(){if(history.value.length){draft.value=history.value.pop();say(message('Change undone.'))}}
function transfer(type,value,dest,pos){
  if(type==='group'){
    const gs=draft.value.groups
    const old=gs.findIndex(g=>g.id===value.id)
    if(old>=0)gs.splice(old,1)
    gs.splice(pos,0,copy(value))
  }else{
    const target=draft.value.groups.find(g=>g.id===dest)
    if(!target)return
    for(const g of draft.value.groups){const idx=g.items.findIndex(i=>i.id===value.id);if(idx>=0){g.items.splice(idx,1);break}}
    target.items.splice(pos,0,copy(value))
  }
}
function commitEditor({value,group:dest,position}){
  change(()=>transfer(modal.value,value,dest,position),message('Draft updated.'))
  close()
}
function remove(){
  const g=group.value, deletingGroup=modal.value==='group'
  change(()=>{
    if(deletingGroup){draft.value.groups=draft.value.groups.filter(v=>v.id!==g.id)}
    else g.items=g.items.filter(i=>i.id!==itemId.value)
  },deletingGroup?message('Category and its services removed.'):message('Service removed.'))
  close()
}
function dragStart(event,type,g,i){
  if(!editing.value)return
  drag.value={type,g,i}
  event.dataTransfer.effectAllowed='move'
  event.dataTransfer.setData('text/plain',i||g)
}
function clearDrag(){drag.value=null;hover.value=null}
function dragOver(event,type,key){
  if(drag.value?.type!==type)return
  event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='move';hover.value=key
}
function drop(event,type,g,i=''){
  if(!drag.value || drag.value.type!==type)return
  event.preventDefault();event.stopPropagation()
  const src=draft.value.groups.find(v=>v.id===drag.value.g)
  const dest=draft.value.groups.find(v=>v.id===g)
  const value=type==='group'?src:src?.items.find(v=>v.id===drag.value.i)
  const before=type==='group'?g:i
  if(value && before!==value.id && (dest || type==='group')){
    const list=(type==='group'?draft.value.groups:dest.items).filter(v=>v.id!==value.id)
    const pos=before?list.findIndex(v=>v.id===before):list.length
    if(pos>=0)change(()=>transfer(type,value,g,pos),message('Order changed.'))
  }
  clearDrag()
}
function endDraft(){draft.value=null;history.value=[];conflict.value=false;preview.value=false;close()}
function cancelDraft(){if(dirty.value)show('discard');else endDraft()}
async function save(){
  if(working.value)return
  working.value=true;error.value=''
  try{
    const result=await api('dashboard','PUT',draft.value)
    applyDocument(result);endDraft();say(message('Dashboard saved.'));void poll()
  }catch(e){
    if(e.status===409){conflict.value=true;error.value=message('The dashboard has changed. Your draft is still open.')}
    else if(e.status===401){authenticated.value=false;show('login');error.value=message('Your session expired. Sign in to save your draft.')}
    else error.value=apiError(e)
  }finally{working.value=false}
}
async function reloadConflict(){
  working.value=true;error.value=''
  try{applyDocument(await api('dashboard'));draft.value=copy(doc.value);history.value=[];conflict.value=false;modal.value='';say(message('Latest version loaded.'))}
  catch(e){error.value=apiError(e)}
  finally{working.value=false}
}
function settings(){proposed.value=copy(data.value.settings);show('settings')}
function applySettings(){
  if(!proposed.value.title.trim()){error.value=message('Enter a title.');return}
  change(()=>{draft.value.settings={...proposed.value,title:proposed.value.title.trim()}},message('Settings applied to draft.'))
  close()
}
function setTheme(value){
  theme.value=value
  if(value==='system')delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme=value
  try{localStorage.setItem('startseite.theme',value)}catch{}
}
async function backup(){
  working.value=true;error.value=''
  try{
    const blob=await api('backup')
    const url=URL.createObjectURL(blob),a=document.createElement('a')
    a.href=url;a.download=(language.value==='de'?'startseite-':'dashboard-')+new Date().toISOString().slice(0,10)+'.zip';a.click()
    setTimeout(()=>URL.revokeObjectURL(url),5000)
  }catch(e){error.value=apiError(e)}
  finally{working.value=false}
}
function pickFile(event){
  const f=event.target.files?.[0]
  if(!f)return
  restoreFile.value=f;event.target.value='';show('restore')
}
async function restore(){
  working.value=true;error.value=''
  try{const result=await api('restore','POST',restoreFile.value);applyDocument(result);endDraft();modal.value='';say(message('Backup restored.'));void poll()}
  catch(e){error.value=apiError(e)}
  finally{working.value=false}
}
async function logout(){
  working.value=true;error.value=''
  try{await api('logout','POST');authenticated.value=false;endDraft();modal.value='';say(message('Signed out.'))}
  catch(e){error.value=apiError(e)}
  finally{working.value=false}
}
function keys(e){
  if(modal.value || e.defaultPrevented)return
  if((e.key==='k' && (e.metaKey||e.ctrlKey)) || (e.key==='/' && !['INPUT','TEXTAREA','SELECT'].includes(e.target?.tagName))){
    if(!editing.value){e.preventDefault();search.value?.focus()}
  }
  if(e.key==='Escape' && document.activeElement===search.value){query.value='';search.value.blur()}
}
function away(e){if(dirty.value){e.preventDefault();e.returnValue=''}}
function resume(){if(document.visibilityState==='visible')void poll()}
function mediaChange(e){osDark.value=e.matches}
watch(pageTitle,title=>{document.title=title},{immediate:true})
watch(language,()=>{
  statusRevision++
  void poll()
})
watch(preview,clearFilters)
onMounted(()=>{
  void load();void poll()
  interval=setInterval(()=>{if(!document.hidden)void poll()},30000)
  window.addEventListener('keydown',keys);window.addEventListener('beforeunload',away)
  document.addEventListener('visibilitychange',resume);themeMedia.addEventListener('change',mediaChange)
})
onUnmounted(()=>{
  clearInterval(interval);clearTimeout(announcementTimer);window.removeEventListener('keydown',keys);window.removeEventListener('beforeunload',away)
  document.removeEventListener('visibilitychange',resume);themeMedia.removeEventListener('change',mediaChange)
})
</script>

<template>
<a class="skip" href="#content">{{t('Skip to content')}}</a>
<header class="topbar">
  <div class="bar-inner">
    <div class="bar-brand">
      <h1 class="brand-heading"><a href="#content" class="brand"><img src="/startseite.svg" alt="" width="30" height="30"><span>{{pageTitle}}</span></a></h1>
      <p v-if="data?.settings.subtitle" class="subtitle">{{data.settings.subtitle}}</p>
    </div>
    <div v-if="summary.length" class="overview" role="group" :aria-label="t('Status overview')">
      <button v-for="[key,label,n] in summary" :key="key" type="button" class="status-filter"
        :aria-pressed="selectedStatus===key" aria-controls="content" :disabled="editing"
        :title="editing?undefined:selectedStatus===key?t('Clear status filter'):t('Filter by this status')"
        @click="selectedStatus=selectedStatus===key?null:key">
        <i aria-hidden="true" class="dot" :class="'state-'+key"></i><span>{{n}} {{label}}</span>
      </button>
    </div>
    <div v-if="doc && !editing" class="search">
      <Glyph name="search" :size="18"/><input ref="search" v-model="query" type="search" :aria-label="t('Search services')" :placeholder="t('Search services …')">
      <button v-if="query" class="clear-search" :aria-label="t('Clear search')" @click="query='';nextTick(()=>search?.focus())"><Glyph name="x" :size="15"/></button><kbd v-else aria-hidden="true">/</kbd>
    </div>
    <div class="bar-actions">
      <LanguagePicker/>
      <button class="icon-button" :aria-label="dark?t('Switch to light theme'):t('Switch to dark theme')" :title="dark?t('Light theme'):t('Dark theme')" @click="setTheme(dark?'light':'dark')"><Glyph :name="dark?'moon':'sun'"/></button>
      <template v-if="doc"><button v-if="!draft" class="button toolbar-edit" @click="begin"><Glyph name="edit" :size="15"/><span>{{t('Edit')}}</span></button>
        <button v-else class="button toolbar-edit" :disabled="working" @click="settings"><Glyph name="settings" :size="16"/><span>{{t('Settings')}}</span></button></template>
    </div>
  </div>
</header>
<main id="content" class="main" tabindex="-1">
  <div v-if="loadError && !doc" class="empty"><Glyph name="warning" :size="30"/><h2>{{t('Dashboard unavailable')}}</h2><p>{{loadError()}}</p><button class="button" @click="load">{{t('Try again')}}</button></div>
  <div v-else-if="!doc" class="empty" role="status">{{t('Loading dashboard …')}}</div>
  <template v-else>
    <div v-if="statusError" class="notice" role="status"><Glyph name="warning"/><span>{{statusError()}}</span><button class="text-button" :disabled="polling" @click="poll">{{t('Check again')}}</button></div>
    <aside v-if="hostNotice" class="host-notice" :class="'host-'+hostNotice.state" :aria-label="t('Infrastructure status')"><Glyph name="server" :size="20"/><div><strong>{{hostNotice.title}}</strong><p>{{hostNotice.detail || t('Only limited infrastructure status information is available.')}}</p><small v-if="hostNotice.checked_at">{{t('Checked {time}',{time:timeText(hostNotice.checked_at)})}}</small></div></aside>
    <div v-if="draft" class="editorbar"><div class="editor-label"><Glyph :name="preview?'eye':'edit'" :size="16"/><strong>{{preview?t('Preview'):t('Editing')}}</strong><small>{{dirty?t('Unsaved changes'):t('Draft')}}</small></div>
      <div class="editor-actions"><button class="icon-button" :disabled="!history.length || working" :title="t('Undo')" :aria-label="t('Undo')" @click="undo"><Glyph name="undo" :size="16"/></button>
      <button class="button small" :disabled="working" @click="preview=!preview">{{preview?t('Continue editing'):t('Preview')}}</button>
      <button class="button small" :disabled="working" @click="cancelDraft">{{t('Finish editing')}}</button>
      <button class="button small primary" :disabled="!dirty || working || conflict" @click="save">{{working && !modal?t('Saving …'):t('Save')}}</button></div></div>
    <div v-if="draft && (conflict || (error && !modal))" class="notice" role="alert"><Glyph name="warning"/><span>{{(error && error())||t('The saved version has changed.')}}</span><button v-if="conflict" class="text-button" @click="show('conflict')">{{t('Load latest version')}}</button></div>
    <div v-if="editing && sourceError" class="notice" role="alert">{{sourceError()}}<button class="text-button" @click="loadSources">{{t('Try again')}}</button></div>
    <div v-if="visibleGroups.length" class="groups">
      <section v-for="g in visibleGroups" :key="g.id" class="group" :aria-labelledby="'group-'+g.id" :class="{'drop-mark':hover==='g-'+g.id}" @dragover="dragOver($event,'group','g-'+g.id)" @drop="drop($event,'group',g.id)">
        <div class="group-heading" :draggable="editing" @dragstart="dragStart($event,'group',g.id)" @dragend="clearDrag">
          <Logo :entry="g" group/><h2 :id="'group-'+g.id">{{g.name}}</h2><span class="group-count">{{g.items.length}}</span><span class="rule"></span>
          <template v-if="editing"><button class="icon-button" :aria-label="t('Edit or move {name}',{name:g.name})" :title="t('Edit or move category')" @click="show('group',g.id)"><Glyph name="edit" :size="15"/></button>
          <button class="icon-button" :aria-label="t('Add service to {name}',{name:g.name})" :title="t('Add service')" @click="show('item',g.id)"><Glyph name="plus"/></button></template>
        </div>
        <div class="cards" :class="{quartet:g.items.length===4}"><div v-for="i in g.items" :key="i.id" class="card-cell" :draggable="editing" :class="{'drop-mark':hover==='i-'+i.id}" @dragstart="dragStart($event,'item',g.id,i.id)" @dragend="clearDrag" @dragover="dragOver($event,'item','i-'+i.id)" @drop="drop($event,'item',g.id,i.id)">
          <ServiceCard :item="i" :status="live(i)" :offline="!!statusError" :editing="editing" :now="now" @edit="show('item',g.id,i.id)" @move="show('move',g.id,i.id)"/></div></div>
        <div v-if="editing && (drag?.type==='item' || !g.items.length)" class="drop-zone" :class="{'drop-mark':hover==='end-'+g.id}" @dragover="dragOver($event,'item','end-'+g.id)" @drop="drop($event,'item',g.id)"><span v-if="drag">{{t('Drop here to add at the end')}}</span><button v-else class="text-button" @click="show('item',g.id)"><Glyph name="plus" :size="14"/>{{t('Add first service')}}</button></div>
        <p v-else-if="!g.items.length" class="hint group-empty">{{t('No services yet.')}}</p>
      </section>
    </div>
    <div v-if="editing && drag?.type==='group'" class="drop-zone" :class="{'drop-mark':hover==='last-group'}" @dragover="dragOver($event,'group','last-group')" @drop="drop($event,'group','')">{{t('Drop category at the end')}}</div>
    <button v-if="editing" class="button add-group" @click="show('group')"><Glyph name="plus" :size="16"/>{{t('Add category')}}</button>
    <div v-if="!visibleGroups.length && !editing" class="empty">
      <template v-if="query || selectedStatus">
        <Glyph name="search" :size="30"/><h2>{{t('No matching services')}}</h2>
        <p>{{query?t('Try a different search term.'):t('No services currently have this status.')}}</p>
        <button class="button" @click="clearFilters">{{selectedStatus?t('Show all services'):t('Reset search')}}</button>
      </template>
      <template v-else><Glyph name="folder" :size="30"/><h2>{{t('No categories yet')}}</h2><p>{{t('Enter edit mode to add your first category.')}}</p></template>
    </div>
    <footer class="page-footer"><span role="status">{{visibleCount}} {{visibleCount===1?t('service'):t('services')}} · {{visibleGroups.length}} {{visibleGroups.length===1?t('category'):t('categories')}}</span><button v-if="!draft" class="text-button" @click="settings"><Glyph name="settings" :size="14"/>{{t('Settings')}}</button></footer>
  </template>
</main>
<div v-if="announcement" class="toast" role="status">{{announcement()}}</div>
<Sheet v-if="modal" :key="modal" :title="modalTitle" :busy="working" @close="close">
  <Editor v-if="['group','item','move'].includes(modal)" :type="modal" :entry="entry" :group-id="groupId" :doc="data" :sources="sources" :now="now" :status="entry?live(entry):null" @apply="commitEditor" @remove="remove" @close="close"/>
  <form v-else-if="modal==='login'" @submit.prevent="login">
    <div class="sheet-body">
      <label>{{t('Password')}}<input v-model="password" autofocus type="password" autocomplete="current-password" required></label>
      <p v-if="error" class="error" role="alert">{{error()}}</p>
    </div>
    <div class="sheet-footer">
      <button type="button" class="button" :disabled="working" @click="close">{{t('Cancel')}}</button>
      <button class="button primary" :disabled="working">{{working?t('Signing in …'):t('Sign in')}}</button>
    </div>
  </form>
  <form v-else-if="modal==='settings'" @submit.prevent="draft?applySettings():close()"><div class="sheet-body">
    <label>{{t('Theme on this device')}}<select :value="theme" @change="setTheme($event.target.value)"><option value="system">{{t('Use system setting')}}</option><option value="light">{{t('Light')}}</option><option value="dark">{{t('Dark')}}</option></select></label>
    <template v-if="draft"><label>{{t('Title')}}<input v-model="proposed.title" required maxlength="160"></label><label>{{t('Subtitle')}}<input v-model="proposed.subtitle" maxlength="400"></label></template>
    <template v-if="authenticated"><div class="settings-section"><h3>{{t('Backup')}}</h3><p class="hint">{{t('The ZIP file contains the saved configuration and local logos.')}}</p><div class="action-row">
      <button type="button" class="button small" :disabled="working" @click="backup"><Glyph name="download" :size="15"/>{{t('Export')}}</button>
      <button type="button" class="button small" :disabled="working || dirty" @click="filePicker.click()"><Glyph name="upload" :size="15"/>{{t('Restore')}}</button><input ref="filePicker" hidden type="file" accept=".zip,application/zip" @change="pickFile"></div></div>
      <p v-if="dirty" class="hint">{{t('Save or discard your draft before restoring a backup or signing out.')}}</p>
      <button type="button" class="text-button" :disabled="dirty || working" @click="logout"><Glyph name="lock" :size="14"/>{{t('Sign out')}}</button></template>
    <button v-else type="button" class="text-button" @click="begin"><Glyph name="lock" :size="15"/>{{t('Sign in to edit')}}</button>
    <p v-if="error" class="error" role="alert">{{error()}}</p>
  </div><div class="sheet-footer"><button v-if="draft" type="button" class="button" :disabled="working" @click="close">{{t('Cancel')}}</button><button class="button primary" :disabled="working">{{draft?t('Apply'):t('Done')}}</button></div></form>
  <template v-else><div class="sheet-body"><p class="question">{{modal==='restore'?t('Replace the saved dashboard and its logos with this backup?'):modal==='conflict'?t('Discard your local draft and load the latest saved version?'):t('Your unsaved changes will be lost.')}}</p><strong v-if="modal==='restore'">{{restoreFile?.name}}</strong><p v-if="error" class="error" role="alert">{{error()}}</p></div>
    <div class="sheet-footer"><button class="button" :disabled="working" @click="close">{{t('Cancel')}}</button><button class="button destructive" :disabled="working" @click="modal==='restore'?restore():modal==='conflict'?reloadConflict():endDraft()">{{modal==='restore'?t('Restore'):modal==='conflict'?t('Load version'):t('Discard')}}</button></div></template>
</Sheet>
</template>
