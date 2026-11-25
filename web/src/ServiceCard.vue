<script setup>
import {computed} from 'vue'
import Glyph from './Glyph.vue'
import Logo from './Logo.vue'
import {safeLink,displayHost,timeText,ageText,serviceState} from './model.js'
const props=defineProps({item:Object,status:Object,offline:Boolean,editing:Boolean,now:Number})
defineEmits(['edit','move'])
const url=computed(()=>safeLink(props.item.url))
const state=computed(()=>serviceState(props.item,props.status,props.offline))
const youtube=computed(()=>props.item.kind==='youtube')
const linkCheck=computed(()=>props.item.kind==='link' && props.item.check_link===true)
const videoUrl=computed(()=>{
  const id=props.status?.latest_video_id
  return youtube.value && typeof id==='string' && /^[A-Za-z0-9_-]{11}$/.test(id)
    ? 'https://www.youtube.com/watch?v='+id : null
})
const label=computed(()=>{
  if (linkCheck.value) return {
    ok:'Webseite erreichbar',warning:'Warnung',down:'Vom Server nicht erreichbar',stale:'Status veraltet',
    unknown:props.status?.checked_at?'Erreichbarkeit unbekannt':'Noch nicht geprüft'
  }[state.value]
  if (!props.status) return 'Noch kein Status'
  return {ok:youtube.value?'Kanäle aktuell':'Erreichbar',warning:'Warnung',down:youtube.value?'Kanäle nicht abrufbar':'Nicht erreichbar',stale:'Veraltet',unknown:'Unbekannt'}[state.value] || 'Unbekannt'
})
const old=computed(()=>state.value==='stale' || state.value==='down' || state.value==='unknown')
const detail=computed(()=>!linkCheck.value || state.value==='unknown' || state.value==='stale' ? props.status?.detail : null)
const metrics=computed(()=>!linkCheck.value && Array.isArray(props.status?.metrics)?props.status.metrics.filter(x=>x?.value!==null && x?.value!==undefined && x.value!==''):[])
const piHole=computed(()=>props.item.kind==='pihole')
const piHoleMain = new Map([
  ['Anfragen', 'Anfragen (24 h)'], ['Anfragen (24 h)', 'Anfragen (24 h)'],
  ['Blockiert', 'Blockiert (24 h)'], ['DNS-Blockierung', 'DNS-Filter']
])
const youtubeOrder=['Neuester Upload','Kanal','Neue Videos (24 h)','Veröffentlicht']
const youtubeTitle=computed(()=>youtube.value?youtubeOrder.slice(0,2).map(label=>metrics.value.find(metric=>metric.label===label)?.value).filter(Boolean).join('\n'):null)
const primaryMetrics=computed(()=>{
  if(youtube.value)return [...metrics.value].sort((a,b)=>{
    const index=metric=>{const order=youtubeOrder.indexOf(metric.label);return order<0?youtubeOrder.length:order}
    return index(a)-index(b)
  })
  if(!piHole.value)return metrics.value
  return metrics.value.filter(metric=>piHoleMain.has(metric.label)).map(metric=>({
    label:piHoleMain.get(metric.label), value:metric.value
  }))
})
const detailMetrics=computed(()=>piHole.value?metrics.value.filter(metric=>!piHoleMain.has(metric.label)):[])
const sourceTime=computed(()=>linkCheck.value || old.value ? props.status?.last_success : props.status?.checked_at)
const stamp=computed(()=>timeText(sourceTime.value))
const stampTitle=computed(()=>stamp.value ? (linkCheck.value?'Zuletzt erfolgreich: ':'Geprüft bzw. zuletzt erfolgreich: ')+stamp.value : '')
const progress=computed(()=>!linkCheck.value && typeof props.status?.progress==='number' && Number.isFinite(props.status.progress)?Math.max(0,Math.min(100,props.status.progress)):null)
</script>
<template><article class="card" :class="{'is-edit':editing,'is-live':!!state}" :title="youtubeTitle||undefined">
  <div class="card-top">
    <Logo :entry="item"/>
    <div class="card-text"><h3><a v-if="!editing && url" class="card-link" :href="url" :target="item.target||'_blank'" :rel="youtube?'noreferrer':'noopener noreferrer'">{{item.name}}</a><span v-else>{{item.name||'Neuer Dienst'}}</span></h3>
      <p v-if="item.subtitle || item.url">{{item.subtitle || displayHost(item.url)}}</p>
    </div>
    <Glyph v-if="!editing && url" name="arrow" :size="15" class="launch-arrow"/>
    <button v-if="editing" type="button" class="icon-button card-edit" :aria-label="item.name+' bearbeiten'" title="Dienst bearbeiten" @click="$emit('edit')"><Glyph name="edit" :size="16"/></button>
  </div>
  <div v-if="item.tag || editing" class="card-meta"><span v-if="item.tag" class="tag">{{item.tag}}</span><button v-if="editing" type="button" class="text-button card-move" @click="$emit('move')"><Glyph name="move" :size="13"/>Verschieben</button></div>
  <div v-if="state" class="card-status">
    <div class="status-line" :title="[detail,stampTitle].filter(Boolean).join(' · ')">
      <span class="status-label"><i aria-hidden="true" class="dot" :class="'state-'+state"></i>{{label}}</span>
      <time v-if="!old && sourceTime" :datetime="new Date(sourceTime*1000).toISOString()" :title="linkCheck ? stampTitle : stamp"><span v-if="linkCheck" class="sr-only">Zuletzt erfolgreich </span>{{ageText(sourceTime,now)}}</time>
    </div>
    <p v-if="detail && !linkCheck" class="status-detail">{{detail}}</p>
    <p v-if="old && sourceTime" class="status-detail">{{item.kind==='nas' && metrics.length?'Letzter Messwert':'Zuletzt erfolgreich'}} {{ageText(sourceTime,now)}}</p>
    <dl v-if="primaryMetrics.length" class="metrics" :class="{'pi-metrics':piHole,'youtube-metrics':youtube}">
      <div v-for="(metric,i) in primaryMetrics" :key="i" :class="{'youtube-latest':youtube && metric.label==='Neuester Upload','youtube-origin':youtube && metric.label==='Kanal'}">
        <dt>{{metric.label}}</dt>
        <dd :dir="youtube?'auto':undefined">
          <a v-if="!editing && videoUrl && metric.label==='Neuester Upload'" class="video-link" :href="videoUrl" :target="item.target||'_blank'" rel="noreferrer" :title="metric.value">{{metric.value}}</a>
          <template v-else>{{metric.value}}</template>
        </dd>
      </div>
    </dl>
    <details v-if="detailMetrics.length" class="metric-details">
      <summary>Weitere Messwerte<span class="sr-only"> für {{item.name}}</span></summary>
      <dl class="metrics"><div v-for="(metric,i) in detailMetrics" :key="i"><dt>{{metric.label}}</dt><dd>{{metric.value}}</dd></div></dl>
    </details>
    <progress v-if="progress!==null" :value="progress" max="100" :aria-label="'Auslastung '+item.name+' '+progress+' Prozent'"></progress>
  </div>
  <p v-else-if="youtube" class="card-status status-detail">Keine YouTube-Kanäle eingerichtet.</p>
</article></template>
