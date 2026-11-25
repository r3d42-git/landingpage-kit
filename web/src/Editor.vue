<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {api,canCheckLink,categoryIcons,copy,entryIcon,id,localLogo,safeLink,sameLinkCheck,sameYouTubeChannels,youtubeChannelLimit} from './model.js'
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
      channelError.value='Der Kanal konnte nicht erkannt werden.'
    }else if(channels.value.some(existing=>existing.id===channel.id)){
      channelError.value='Dieser Kanal ist bereits hinzugefügt.'
    }else{
      form.value.channels=[...channels.value,{id:channel.id,name:channel.name}]
      channelInput.value=''
      channelMessage.value='Kanal hinzugefügt.'
    }
  }catch(e){
    if(channelRequest===request)channelError.value=request.signal.aborted?'Die Kanalabfrage dauert zu lange. Bitte erneut versuchen.':e.message
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
  channelMessage.value='Kanal entfernt.'
}
function submit(){
  if(resolving.value)return
  const value=copy(form.value)
  value.name=value.name.trim()
  if(!value.name) {error.value='Bitte einen Namen eingeben.';return}
  if(!move && !groupMode){
    value.url=value.url.trim()
    if(!safeLink(value.url) || !/^(https?:\/\/|\/(?!\/))/i.test(value.url)) {error.value='Bitte eine HTTP(S)-Adresse oder einen lokalen Pfad mit / angeben.';return}
    if(needsSource.value && !value.source) {error.value='Bitte eine verfügbare Messquelle auswählen.';return}
    if(value.kind==='link' || !value.source) delete value.source
    if(value.kind==='link' && value.check_link===true){
      if(!linkCheckAvailable.value) {error.value='Für die Erreichbarkeitsprüfung bitte eine vollständige HTTP(S)-Adresse angeben.';return}
    }else delete value.check_link
    if(value.kind==='youtube'){
      if(channelInput.value.trim()){
        channelError.value ||= 'Bitte den eingegebenen Kanal hinzufügen oder das Eingabefeld leeren.'
        return
      }
      value.channels=channels.value
    }else delete value.channels
  }
  if(!move && value.logo && value.logo!==initial.logo && !localLogo(value.logo)) {error.value='Bitte eine lokale Logo-Datei unter /assets/ angeben.';return}
  emit('apply',{value,group:dest.value,position:pos.value})
}
</script>
<template><form @submit.prevent="submit">
  <div class="sheet-body">
    <template v-if="!move">
      <label>Name<input v-model="form.name" required maxlength="160" autofocus :placeholder="groupMode?'Name der Kategorie':'Name des Dienstes'"></label>
      <fieldset v-if="groupMode" class="category-icons">
        <legend class="field-caption">Kategoriesymbol</legend>
        <div class="icon-options">
          <label v-for="[key,label] in categoryIcons" :key="key" class="icon-option">
            <input type="radio" name="category-icon" :value="key" :checked="selectedIcon===key" @click="chooseIcon(key)" @change="chooseIcon(key)">
            <span><Glyph :name="key" :size="19"/>{{label}}</span>
          </label>
        </div>
        <p v-if="localLogo(form.logo)" class="hint">Ein Symbol aus der Auswahl ersetzt das lokale Logo.</p>
      </fieldset>
      <template v-if="!groupMode">
        <label>Adresse<input v-model="form.url" maxlength="2048" required inputmode="url" placeholder="https:// oder /lokaler-pfad" autocapitalize="none" spellcheck="false"></label>
        <label>Beschreibung<input v-model="form.subtitle" maxlength="400"></label>
        <div class="field-row"><label>Überwachung<select v-model="form.kind" @change="changeKind"><option value="link">Nur Verknüpfung</option><option value="http">HTTP-Erreichbarkeit</option><option value="pihole">Pi-hole</option><option value="nas">NAS</option><option value="service">Dienstmetriken</option><option value="youtube">YouTube-Kanäle</option></select></label>
          <label v-if="needsSource || form.kind==='http'">Messquelle<select v-model="form.source" :required="needsSource"><option value="" :disabled="needsSource">{{needsSource?'Quelle auswählen':'Adresse des Dienstes'}}</option><option v-if="form.source && !sourceOptions.some(x=>x.id===form.source)" :value="form.source">{{form.source}} (derzeit nicht verfügbar)</option><option v-for="s in sourceOptions" :key="s.id" :value="s.id">{{s.label}}</option></select></label>
        </div>
        <div v-if="form.kind==='link'" class="link-check-field">
          <label>
            <input v-model="form.check_link" type="checkbox" :disabled="!linkCheckAvailable && !form.check_link" aria-describedby="link-check-hint">
            Erreichbarkeit prüfen
          </label>
          <p id="link-check-hint" class="hint">Für öffentliche HTTP(S)-Adressen und interne Ziele, die für Prüfungen freigegeben sind.</p>
        </div>
        <p v-if="needsSource && !sourceOptions.length" class="hint">Für diesen Typ ist aktuell keine Messquelle verfügbar.</p>
        <fieldset v-if="form.kind==='youtube'" class="channel-fields">
          <legend class="field-caption">YouTube-Kanäle</legend>
          <ul v-if="channels.length" class="channel-list">
            <li v-for="channel in channels" :key="channel.id">
              <div class="channel-identity">
                <strong dir="auto">{{channel.name}}</strong>
                <small dir="ltr">{{channel.id}}</small>
              </div>
              <button type="button" class="icon-button" :aria-label="channel.name+' entfernen'" title="Kanal entfernen" :disabled="resolving" @click="removeChannel(channel)"><Glyph name="x" :size="16"/></button>
            </li>
          </ul>
          <p v-else class="hint">Füge einen öffentlichen Kanal hinzu, um neue Videos auf der Kachel zu sehen.</p>
          <div class="channel-add">
            <label for="youtube-channel">Kanal hinzufügen</label>
            <input id="youtube-channel" v-model="channelInput" maxlength="2048" placeholder="@Handle, Kanal-ID oder URL" autocapitalize="none" autocomplete="off" spellcheck="false" :readonly="resolving" :disabled="channelLimitReached" :aria-invalid="channelError ? 'true' : undefined" :aria-describedby="channelError ? 'channel-error' : undefined" @input="channelError=''" @keydown.enter.prevent="!$event.isComposing && addChannel()">
            <button type="button" class="button" :disabled="resolving || !channelInput.trim() || channelLimitReached" @click="addChannel">Hinzufügen</button>
          </div>
          <p v-if="channelLimitReached" class="hint">Maximal {{youtubeChannelLimit}} Kanäle pro Kachel.</p>
          <p role="status" :class="{'sr-only':!resolving}" class="hint">{{resolving?'Kanal wird gesucht …':channelMessage}}</p>
          <p v-if="channelError" id="channel-error" role="alert" class="error">{{channelError}}</p>
        </fieldset>
      </template>
      <details class="advanced"><summary>Weitere Optionen</summary><div class="more-fields">
        <label>Lokales Logo<input v-model="form.logo" maxlength="2048" placeholder="/assets/…" spellcheck="false"></label>
        <label>{{groupMode?'Symbolkennung':'Symbol'}}<input v-model="form.icon" maxlength="120" placeholder="z. B. server, shield oder folder"></label>
        <template v-if="!groupMode"><label>Schlagwort<input v-model="form.tag" maxlength="80"></label>
        <label>Link öffnen<select v-model="form.target"><option value="_blank">In neuem Tab</option><option value="_self">In diesem Tab</option></select></label></template>
      </div></details>
    </template>
    <label v-if="!groupMode">Kategorie<select v-model="dest"><option v-for="g in doc.groups" :key="g.id" :value="g.id">{{g.name}}</option></select></label>
    <label>Position<select v-model.number="pos"><option v-for="(entry,i) in slots" :key="entry.id" :value="i">{{i+1}} · Vor {{entry.name}}</option><option :value="slots.length">{{slots.length+1}} · Am Ende</option></select></label>
    <div v-if="!move" class="preview"><span class="field-caption">Vorschau</span><div v-if="groupMode" class="preview-group"><Logo :entry="form" group/><strong>{{form.name||'Neue Kategorie'}}</strong></div><ServiceCard v-else :item="form" :status="previewStatus" :now="now" inert/></div>
    <p v-if="error" role="alert" class="error">{{error}}</p>
  </div>
  <div class="sheet-footer"><button v-if="exists && !move" type="button" class="icon-button danger" :aria-label="groupMode?'Kategorie löschen':'Dienst löschen'" title="Löschen" @click="emit('remove')"><Glyph name="trash"/></button>
    <span class="spacer"></span><button type="button" class="button" @click="emit('close')">Abbrechen</button><button class="button primary" :disabled="resolving">{{move?'Verschieben':'Übernehmen'}}</button></div>
</form></template>
