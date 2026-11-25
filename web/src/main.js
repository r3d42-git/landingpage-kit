import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

try {
  const saved = localStorage.getItem('startseite.theme')
  if (['light', 'dark'].includes(saved)) document.documentElement.dataset.theme = saved
} catch { /* Private browsing may restrict storage. */ }
createApp(App).mount('#app')
