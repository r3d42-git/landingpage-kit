<script setup>
import {localizeRequired,resetValidation,t} from './i18n.js'
import {onMounted,ref} from 'vue'
import LanguagePicker from './LanguagePicker.vue'
import Glyph from './Glyph.vue'
defineProps({title:String,busy:Boolean})
const emit=defineEmits(['close'])
const dialog=ref(null)
onMounted(()=>dialog.value.showModal())
</script>
<template><dialog @invalid.capture="localizeRequired" @input="resetValidation" ref="dialog" class="sheet" aria-labelledby="sheet-title" @cancel.prevent="!busy && emit('close')" @click="event=>{if(event.target===dialog && !busy){const b=dialog.getBoundingClientRect();if(event.clientX<b.left || event.clientX>b.right || event.clientY<b.top || event.clientY>b.bottom) emit('close')}}">
  <div class="sheet-heading"><h2 id="sheet-title">{{title}}</h2><LanguagePicker/><button type="button" class="icon-button" :aria-label="t('Close')" :disabled="busy" @click="emit('close')"><Glyph name="x"/></button></div>
  <slot/>
</dialog></template>
