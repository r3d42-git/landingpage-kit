<script setup>
import Glyph from './Glyph.vue'
import {language,setLanguage,t} from './i18n.js'
</script>

<template>
  <label class="language-picker" :title="t('Language')">
    <Glyph name="globe" :size="16"/>
    <span aria-hidden="true">{{language.toUpperCase()}}</span>
    <select :value="language" :aria-label="t('Language')" @change="setLanguage($event.target.value)">
      <option value="de" lang="de">Deutsch</option>
      <option value="en" lang="en">English</option>
    </select>
  </label>
</template>
