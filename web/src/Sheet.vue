<script setup>
import {onMounted,ref} from 'vue'
import Glyph from './Glyph.vue'
defineProps({title:String,busy:Boolean})
const emit=defineEmits(['close'])
const dialog=ref(null)
onMounted(()=>dialog.value.showModal())
</script>
<template><dialog ref="dialog" class="sheet" aria-labelledby="sheet-title" @cancel.prevent="!busy && emit('close')" @click="event=>{if(event.target===dialog && !busy){const b=dialog.getBoundingClientRect();if(event.clientX<b.left || event.clientX>b.right || event.clientY<b.top || event.clientY>b.bottom) emit('close')}}">
  <div class="sheet-heading"><h2 id="sheet-title">{{title}}</h2><button type="button" class="icon-button" aria-label="Schließen" :disabled="busy" @click="emit('close')"><Glyph name="x"/></button></div>
  <slot/>
</dialog></template>
