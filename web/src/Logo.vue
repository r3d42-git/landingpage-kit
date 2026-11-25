<script setup>
import {computed, ref, watch} from 'vue'
import Glyph from './Glyph.vue'
import {entryIcon,localLogo,tone} from './model.js'

const props=defineProps({entry:Object,group:Boolean})
const failed=ref(false)
const url=computed(()=>localLogo(props.entry.logo))
watch(url,()=>{failed.value=false})
const icon=computed(()=>entryIcon(props.entry,props.group))
</script>
<template><span class="logo" :class="[group?'group-logo':'tile-logo','tone-'+tone(entry.name),{'has-logo':url && !failed}]">
  <img v-if="url && !failed" :src="url" alt="" @error="failed=true"><Glyph v-else :name="icon" :size="group?19:26"/>
</span></template>
