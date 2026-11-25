<script setup>
import {message,t} from './i18n.js'
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {api,apiError,canCheckLink,categoryIcons,copy,entryIcon,id,localLogo,safeLink,sameLinkCheck,sameYouTubeChannels,youtubeChannelLimit} from './model.js'
import Glyph from './Glyph.vue'
import Logo from './Logo.vue'
import ServiceCard from './ServiceCard.vue'

const props=defineProps({type:String,entry:Object,groupId:String,doc:Object,sources:Array,status:Object,now:Number})
const emit=defineEmits(['apply','remove','close'])
const group=props.doc.groups.find(g=>g.id===props.groupId)
const groupMode=props.type==='group'
const move=props.type==='move'
const exists=Boolean(props.entry)
const initial=props.entry || (groupMode?{id:id(),name:'',items:[]}:{id:id(),name:'',url:'',subtitle:'',tag:'',kind:'link',target:'_blank'})
const form=ref(copy(initial))
const selectedIcon=computed(()=>localLogo(form.value.logo) ? null : entryIcon(form.value,true))
function chooseIcon(icon){
  form.value.icon=icon
  delete form.value.logo
}
if (!groupMode) form.value.source ||= ''
const error=ref('')
const dest=ref(props.groupId||props.doc.groups[0]?.id)
const slots=computed(()=>groupMode?props.doc.groups.filter(x=>x.id!==form.value.id):props.doc.groups.find(g=>g.id===dest.value)?.items.filter(x=>x.id!==form.value.id)||[])
const oldPos=groupMode?props.doc.groups.findIndex(x=>x.id===form.value.id):group?.items.findIndex(x=>x.id===form.value.id)
const pos=ref(oldPos>=0?oldPos:slots.value.length)
watch(dest,()=>{pos.value=slots.value.length})
const sourceOptions=computed(()=>(props.sources||[]).filter(x=>x.kind===form.value.kind))
const needsSource=computed(()=>['service','nas','pihole'].includes(form.value.kind))
const linkCheckAvailable=computed(()=>canCheckLink(form.value.url?.trim()))
const channels=computed(()=>form.value.channels || [])
const channelLimitReached=computed(()=>channels.value.length>=youtubeChannelLimit)
const channelInput=ref('')
const channelError=ref('')
const channelMessage=ref('')
const resolving=ref(false)
let channelRequest
const previewStatus=computed(()=>{
  if(form.value.kind!==initial.kind || (form.value.source||'')!==(initial.source||'') || form.value.url!==initial.url)return null
  if(form.value.kind==='link' && !sameLinkCheck(form.value,initial))return null
  if(form.value.kind==='youtube' && !sameYouTubeChannels(channels.value,initial.channels))return null
  return props.status
})
function resetChannelRequest(){
  channelRequest?.abort()
  channelRequest=undefined
  resolving.value=false
}
onBeforeUnmount(resetChannelRequest)
function changeKind(){
  resetChannelRequest()
  channelInput.value=''
  channelError.value=''
  channelMessage.value=''
  form.value.source=''
  if(form.value.kind!=='link')delete form.value.check_link
  if(needsSource.value && sourceOptions.value.length===1) form.value.source=sourceOptions.value[0].id
}
async function addChannel(){
  const input=channelInput.value.trim()
  if(!input || resolving.value || channelLimitReached.value)return
  const request=new AbortController()
  channelRequest=request
  resolving.value=true
  channelError.value=''
  channelMessage.value=''
  const timeout=setTimeout(()=>request.abort(),15000)
  try{
    const {channel}=await api('youtube/resolve','POST',{input},request.signal)
    if(channelRequest!==request)return
    if(!channel || !/^UC[A-Za-z0-9_-]{22}$/.test(channel.id) || typeof channel.name!=='string' || !channel.name.trim()){
      channelError.value=message('Could not identify the channel.')
    }else if(channels.value.some(existing=>existing.id===channel.id)){
      channelError.value=message('This channel has already been added.')
    }else{
      form.value.channels=[...channels.value,{id:channel.id,name:channel.name}]
      channelInput.value=''
      channelMessage.value=message('Channel added.')
    }
  }catch(e){
    if(channelRequest===request)channelError.value=request.signal.aborted?message('The channel lookup timed out. Try again.'):apiError(e)
  }finally{
    clearTimeout(timeout)
    if(channelRequest===request){
      channelRequest=undefined
      resolving.value=false
    }
  }
}
function removeChannel(channel){
  form.value.channels=channels.value.filter(existing=>existing.id!==channel.id)
  channelError.value=''
  channelMessage.value=message('Channel removed.')
}
function submit(){
  if(resolving.value)return
  const value=copy(form.value)
  value.name=value.name.trim()
  if(!value.name) {error.value=message('Enter a name.');return}
  if(!move && !groupMode){
    value.url=value.url.trim()
    if(!safeLink(value.url) || !/^(https?:\/\/|\/(?!\/))/i.test(value.url)) {error.value=message('Enter an HTTP(S) address or a local path starting with /.');return}
    if(needsSource.value && !value.source) {error.value=message('Select an available data source.');return}
    if(value.kind==='link' || !value.source) delete value.source
    if(value.kind==='link' && value.check_link===true){
      if(!linkCheckAvailable.value) {error.value=message('Enter a complete HTTP(S) address for the availability check.');return}
    }else delete value.check_link
    if(value.kind==='youtube'){
      if(channelInput.value.trim()){
        channelError.value ||= message('Add the channel you entered or clear the input.')
        return
      }
      value.channels=channels.value
    }else delete value.channels
  }
  if(!move && value.logo && value.logo!==initial.logo && !localLogo(value.logo)) {error.value=message('Enter the path to a local logo file under /assets/.');return}
  emit('apply',{value,group:dest.value,position:pos.value})
}
</script>
<template><form @submit.prevent="submit">
  <div class="sheet-body">
    <template v-if="!move">
      <label>Name<input v-model="form.name" required maxlength="160" autofocus :placeholder="groupMode?t('Category name'):t('Service name')"></label>
      <fieldset v-if="groupMode" class="category-icons">
        <legend class="field-caption">{{t('Category icon')}}</legend>
        <div class="icon-options">
          <label v-for="[key,label] in categoryIcons" :key="key" class="icon-option">
            <input type="radio" name="category-icon" :value="key" :checked="selectedIcon===key" @click="chooseIcon(key)" @change="chooseIcon(key)">
            <span><Glyph :name="key" :size="19"/>{{t(label)}}</span>
          </label>
        </div>
        <p v-if="localLogo(form.logo)" class="hint">{{t('Choosing an icon replaces the local logo.')}}</p>
      </fieldset>
      <template v-if="!groupMode">
        <label>{{t('Address')}}<input v-model="form.url" maxlength="2048" required inputmode="url" :placeholder="t('https:// or /local-path')" autocapitalize="none" spellcheck="false"></label>
        <label>{{t('Description')}}<input v-model="form.subtitle" maxlength="400"></label>
        <div class="field-row"><label>{{t('Monitoring')}}<select v-model="form.kind" @change="changeKind"><option value="link">{{t('Link only')}}</option><option value="http">{{t('HTTP availability')}}</option><option value="pihole">Pi-hole</option><option value="nas">NAS</option><option value="service">{{t('Service metrics')}}</option><option value="youtube">{{t('YouTube channels')}}</option></select></label>
          <label v-if="needsSource || form.kind==='http'">{{t('Data source')}}<select v-model="form.source" :required="needsSource"><option value="" :disabled="needsSource">{{needsSource?t('Select a source'):t('Service address')}}</option><option v-if="form.source && !sourceOptions.some(x=>x.id===form.source)" :value="form.source">{{t('{source} (currently unavailable)',{source:form.source})}}</option><option v-for="s in sourceOptions" :key="s.id" :value="s.id">{{s.label}}</option></select></label>
        </div>
        <div v-if="form.kind==='link'" class="link-check-field">
          <label>
            <input v-model="form.check_link" type="checkbox" :disabled="!linkCheckAvailable && !form.check_link" aria-describedby="link-check-hint">
            {{t('Check availability')}}
          </label>
          <p id="link-check-hint" class="hint">{{t('For public HTTP(S) addresses and internal destinations approved for availability checks.')}}</p>
        </div>
        <p v-if="needsSource && !sourceOptions.length" class="hint">{{t('No data source is currently available for this type.')}}</p>
        <fieldset v-if="form.kind==='youtube'" class="channel-fields">
          <legend class="field-caption">{{t('YouTube channels')}}</legend>
          <ul v-if="channels.length" class="channel-list">
            <li v-for="channel in channels" :key="channel.id">
              <div class="channel-identity">
                <strong dir="auto">{{channel.name}}</strong>
                <small dir="ltr">{{channel.id}}</small>
              </div>
              <button type="button" class="icon-button" :aria-label="t('Remove {name}',{name:channel.name})" :title="t('Remove channel')" :disabled="resolving" @click="removeChannel(channel)"><Glyph name="x" :size="16"/></button>
            </li>
          </ul>
          <p v-else class="hint">{{t('Add a public channel to see new videos on this tile.')}}</p>
          <div class="channel-add">
            <label for="youtube-channel">{{t('Add channel')}}</label>
            <input id="youtube-channel" v-model="channelInput" maxlength="2048" :placeholder="t('@handle, channel ID or URL')" autocapitalize="none" autocomplete="off" spellcheck="false" :readonly="resolving" :disabled="channelLimitReached" :aria-invalid="channelError ? 'true' : undefined" :aria-describedby="channelError ? 'channel-error' : undefined" @input="channelError=''" @keydown.enter.prevent="!$event.isComposing && addChannel()">
            <button type="button" class="button" :disabled="resolving || !channelInput.trim() || channelLimitReached" @click="addChannel">{{t('Add')}}</button>
          </div>
          <p v-if="channelLimitReached" class="hint">{{t('Up to {count} channels per tile.',{count:youtubeChannelLimit})}}</p>
          <p role="status" :class="{'sr-only':!resolving}" class="hint">{{resolving?t('Looking up channel …'):channelMessage && channelMessage()}}</p>
          <p v-if="channelError" id="channel-error" role="alert" class="error">{{channelError()}}</p>
        </fieldset>
      </template>
      <details class="advanced"><summary>{{t('More options')}}</summary><div class="more-fields">
        <label>{{t('Local logo')}}<input v-model="form.logo" maxlength="2048" placeholder="/assets/…" spellcheck="false"></label>
        <label>{{groupMode?t('Icon identifier'):t('Icon')}}<input v-model="form.icon" maxlength="120" :placeholder="t('e.g. server, shield or folder')"></label>
        <template v-if="!groupMode"><label>{{t('Tag')}}<input v-model="form.tag" maxlength="80"></label>
        <label>{{t('Open link')}}<select v-model="form.target"><option value="_blank">{{t('In a new tab')}}</option><option value="_self">{{t('In this tab')}}</option></select></label></template>
      </div></details>
    </template>
    <label v-if="!groupMode">{{t('Category')}}<select v-model="dest"><option v-for="g in doc.groups" :key="g.id" :value="g.id">{{g.name}}</option></select></label>
    <label>Position<select v-model.number="pos"><option v-for="(entry,i) in slots" :key="entry.id" :value="i">{{i+1}} · {{t('Before {name}',{name:entry.name})}}</option><option :value="slots.length">{{slots.length+1}} · {{t('At the end')}}</option></select></label>
    <div v-if="!move" class="preview"><span class="field-caption">{{t('Preview')}}</span><div v-if="groupMode" class="preview-group"><Logo :entry="form" group/><strong>{{form.name||t('New category')}}</strong></div><ServiceCard v-else :item="form" :status="previewStatus" :now="now" inert/></div>
    <p v-if="error" role="alert" class="error">{{error()}}</p>
  </div>
  <div class="sheet-footer"><button v-if="exists && !move" type="button" class="icon-button danger" :aria-label="groupMode?t('Delete category'):t('Delete service')" :title="t('Delete')" @click="emit('remove')"><Glyph name="trash"/></button>
    <span class="spacer"></span><button type="button" class="button" @click="emit('close')">{{t('Cancel')}}</button><button class="button primary" :disabled="resolving">{{move?t('Move'):t('Apply')}}</button></div>
</form></template>
