<template>
  <header class="chat-header">
    <div class="brand">
      <span
        class="brand-orb"
        aria-hidden="true"
      ></span>
      <h1>Agent</h1>
    </div>
    <div class="service-status">
      <span
        class="status-dot"
        aria-hidden="true"
      ></span>
      服务已连接
    </div>
  </header>
</template>

<style scoped>
.chat-header { height:58px; padding:0 28px; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:4; border-bottom:1px solid rgb(0 0 0 / 6%); background:rgb(250 250 252 / 76%); backdrop-filter:saturate(180%) blur(22px); }
.brand { display:flex; align-items:center; gap:10px; letter-spacing:-.02em; }
.brand h1 { margin:0; font-size:16px; line-height:1; }
.brand-orb { width:27px; height:27px; border-radius:9px; background:conic-gradient(from 210deg,#725cff,#45b7ff,#c76cff,#725cff); box-shadow:0 5px 15px rgb(99 91 255 / 28%); }
.service-status { display:flex; align-items:center; gap:8px; color:var(--agent-muted); font-size:12px; }
.status-dot { width:7px; height:7px; border-radius:50%; background:#30d158; box-shadow:0 0 0 4px rgb(48 209 88 / 10%); }
@media (max-width:640px) { .chat-header { padding:0 17px; } }
</style>
