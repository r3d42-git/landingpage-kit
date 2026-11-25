<script setup>
import {language,t} from './i18n.js'
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
    ok:t('Website reachable'),warning:t('Warning'),down:t('Unreachable from server'),stale:t('Status out of date'),
    unknown:props.status?.checked_at?t('Availability unknown'):t('Not checked yet')
  }[state.value]
  if (!props.status) return t('No status yet')
  return {ok:youtube.value?t('Channels up to date'):t('Reachable'),warning:t('Warning'),down:youtube.value?t('Channels unavailable'):t('Unreachable'),stale:t('Stale'),unknown:t('Unknown')}[state.value] || t('Unknown')
})
const old=computed(()=>state.value==='stale' || state.value==='down' || state.value==='unknown')
const detail=computed(()=>!linkCheck.value || state.value==='unknown' || state.value==='stale' ? props.status?.detail : null)
const metrics=computed(()=>!linkCheck.value && Array.isArray(props.status?.metrics)?props.status.metrics.filter(x=>x?.value!==null && x?.value!==undefined && x.value!==''):[])
const piHole=computed(()=>props.item.kind==='pihole')
const piHoleMain=new Map([['queries','Queries (24 h)'],['blocked','Blocked (24 h)'],['dns_blocking','DNS filter']])
const youtubeOrder=['latest_upload','channel','new_videos_24h','published']
const youtubeTitle=computed(()=>youtube.value?youtubeOrder.slice(0,2).map(key=>metrics.value.find(metric=>metric.key===key)?.value).filter(Boolean).join('\n'):null)
const primaryMetrics=computed(()=>{
  if(youtube.value)return [...metrics.value].sort((a,b)=>{
    const index=metric=>{const order=youtubeOrder.indexOf(metric.key);return order<0?youtubeOrder.length:order}
    return index(a)-index(b)
  })
  if(!piHole.value)return metrics.value
  return metrics.value.filter(metric=>piHoleMain.has(metric.key)).map(metric=>({
    ...metric, label:t(piHoleMain.get(metric.key))
  }))
})
const detailMetrics=computed(()=>piHole.value?metrics.value.filter(metric=>!piHoleMain.has(metric.key)):[])
function metricText(value){
  return typeof value==='number'?value.toLocaleString(language.value):value
}
const sourceTime=computed(()=>linkCheck.value || old.value ? props.status?.last_success : props.status?.checked_at)
const stamp=computed(()=>timeText(sourceTime.value))
const stampTitle=computed(()=>stamp.value ? t(linkCheck.value?'Last successful check: {time}':'Checked or last successful: {time}',{time:stamp.value}) : '')
const progress=computed(()=>!linkCheck.value && typeof props.status?.progress==='number' && Number.isFinite(props.status.progress)?Math.max(0,Math.min(100,props.status.progress)):null)
</script>
<template><article class="card" :class="{'is-edit':editing,'is-live':!!state}" :title="youtubeTitle||undefined">
  <div class="card-top">
    <Logo :entry="item"/>
    <div class="card-text"><h3><a v-if="!editing && url" class="card-link" :href="url" :target="item.target||'_blank'" :rel="youtube?'noreferrer':'noopener noreferrer'">{{item.name}}</a><span v-else>{{item.name||t('New service')}}</span></h3>
      <p v-if="item.subtitle || item.url">{{item.subtitle || displayHost(item.url)}}</p>
    </div>
    <Glyph v-if="!editing && url" name="arrow" :size="15" class="launch-arrow"/>
    <button v-if="editing" type="button" class="icon-button card-edit" :aria-label="t('Edit {name}',{name:item.name})" :title="t('Edit service')" @click="$emit('edit')"><Glyph name="edit" :size="16"/></button>
  </div>
  <div v-if="item.tag || editing" class="card-meta"><span v-if="item.tag" class="tag">{{item.tag}}</span><button v-if="editing" type="button" class="text-button card-move" @click="$emit('move')"><Glyph name="move" :size="13"/>{{t('Move')}}</button></div>
  <div v-if="state" class="card-status">
    <div class="status-line" :title="[detail,stampTitle].filter(Boolean).join(' · ')">
      <span class="status-label"><i aria-hidden="true" class="dot" :class="'state-'+state"></i>{{label}}</span>
      <time v-if="!old && sourceTime" :datetime="new Date(sourceTime*1000).toISOString()" :title="linkCheck ? stampTitle : stamp"><span v-if="linkCheck" class="sr-only">{{t('Last successful check')}} </span>{{ageText(sourceTime,now)}}</time>
    </div>
    <p v-if="detail && !linkCheck" class="status-detail">{{detail}}</p>
    <p v-if="old && sourceTime" class="status-detail">{{item.kind==='nas' && metrics.length?t('Last reading'):t('Last successful check')}} {{ageText(sourceTime,now)}}</p>
    <dl v-if="primaryMetrics.length" class="metrics" :class="{'pi-metrics':piHole,'youtube-metrics':youtube}">
      <div v-for="(metric,i) in primaryMetrics" :key="i" :class="{'youtube-latest':youtube && metric.key==='latest_upload','youtube-origin':youtube && metric.key==='channel'}">
        <dt>{{metric.label}}</dt>
        <dd :dir="youtube?'auto':undefined">
          <a v-if="!editing && videoUrl && metric.key==='latest_upload'" class="video-link" :href="videoUrl" :target="item.target||'_blank'" rel="noreferrer" :title="metric.value">{{metricText(metric.value)}}</a>
          <template v-else>{{metricText(metric.value)}}</template>
        </dd>
      </div>
    </dl>
    <details v-if="detailMetrics.length" class="metric-details">
      <summary>{{t('More metrics')}}<span class="sr-only">{{t(' for {name}',{name:item.name})}}</span></summary>
      <dl class="metrics"><div v-for="(metric,i) in detailMetrics" :key="i"><dt>{{metric.label}}</dt><dd>{{metricText(metric.value)}}</dd></div></dl>
    </details>
    <progress v-if="progress!==null" :value="progress" max="100" :aria-label="t('{name} utilization: {percent}',{name:item.name,percent:(progress/100).toLocaleString(language,{style:'percent'})})"></progress>
  </div>
  <p v-else-if="youtube" class="card-status status-detail">{{t('No YouTube channels configured.')}}</p>
</article></template>
