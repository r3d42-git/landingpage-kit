<script setup>
defineProps({name:String, size:{type:Number,default:18}})
const paths = {
 grid:'M3 4h7v7H3z M14 4h7v7h-7z M3 15h7v6H3z M14 15h7v6h-7z',
 folder:'M3 8V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
 search:'M17 17l4 4 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 edit:'m4 16-1 5 5-1L20 8l-4-4z M13.5 6.5l4 4',
 sun:'M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5 M16.5 12a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0',
 moon:'M20.5 14A8.5 8.5 0 0 1 10 3.5 9 9 0 1 0 20.5 14z',
 settings:'M3 6h8 M15 6h6 M3 12h2 M9 12h12 M3 18h11 M18 18h3 M11 4v4 M7 10v4 M16 16v4',
 arrow:'M7 17 18 6 M7.5 6H18v10.5', down:'m6 9 6 6 6-6', up:'m6 15 6-6 6 6',
 plus:'M12 4v16 M4 12h16', x:'m6 6 12 12 M18 6 6 18',
 link:'m10 14 4-4 M8.5 16.5 7 18a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0 M15.5 7.5 17 6a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0',
 server:'M4 3h16v7H4z M4 14h16v7H4z M16 6.5h1 M16 17.5h1',
 nas:'M4 7c0-5 16-5 16 0s-16 5-16 0z M4 7v10c0 5 16 5 16 0V7 M4 12c0 5 16 5 16 0',
 network:'M12 8v5 M5 16v-3h14v3 M9 3h6v5H9z M2 16h6v5H2z M16 16h6v5h-6z',
 layers:'m12 3 9 4.5-9 4.5-9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5',
 'play-square':'M5 4.5h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2z M10 8.5l5 3.5-5 3.5z',
 shield:'m12 2 8 3v6c0 5-4 8.5-8 11-4-2.5-8-6-8-11V5z M8.5 11.5l2.5 2.5 4.5-5',
 globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M3 12h18 M12 3c5 5 5 13 0 18-5-5-5-13 0-18z',
 tools:'M15 3a6 6 0 0 0-6 8L3.5 17a2.5 2.5 0 0 0 3.5 3.5l6-6a6 6 0 0 0 8-7L17 11l-4-4 4-4z',
 home:'M2 11 12 3l10 8 M5 10v11h5v-7h4v7h5V10',
 lock:'M6 10h12v11H6z M8 10V7a4 4 0 0 1 8 0v3',
 undo:'M3 5v6h6 M3 11a9 9 0 0 1 16 7',
 move:'M12 2v20 M2 12h20 M8 6l4-4 4 4 M8 18l4 4 4-4 M18 8l4 4-4 4 M6 8l-4 4 4 4',
 refresh:'M20 7v5h-5 M20 12a8 8 0 1 0-2 5',
 warning:'M12 3 2 21h20z M12 9v5 M12 17v.1',
 download:'M12 3v12m-5-5 5 5 5-5 M4 15v5h16v-5',
 upload:'M12 16V4m-5 5 5-5 5 5 M4 16v4h16v-4',
 more:'M4 12h1 M11.5 12h1 M19 12h1',
 monitor:'M3 4h18v12H3z M12 16v4 M8 21h8',
 'monitor-activity':'M3 4h18v12H3z M12 16v4 M8 21h8 M5.5 10.5H8l2-4 3 7 2-4 1 1h2.5',
 trash:'M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 9v8 M14 9v8',
 eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'
}
</script>
<template><svg aria-hidden="true" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path :d="paths[name] || paths.grid"/></svg></template>
