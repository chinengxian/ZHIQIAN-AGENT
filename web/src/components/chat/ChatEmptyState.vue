<template>
  <section class="empty-state">
    <div
      class="hero-orb"
      aria-hidden="true"
    ></div>
    <h2>今天想聊些什么？</h2>
    <p>简单、快速、专注于这一段对话。</p>
  </section>
</template>

<style scoped>
.empty-state { margin:auto; padding:48px 20px 80px; text-align:center; }
.hero-orb { width:56px; height:56px; margin:0 auto 20px; border-radius:18px; background:conic-gradient(from 210deg,#725cff,#45b7ff,#c76cff,#725cff); box-shadow:0 10px 28px rgb(99 91 255 / 25%); }
h2 { margin:0; font-size:clamp(34px,5vw,54px); line-height:1.04; letter-spacing:-.052em; }
p { margin:13px 0 0; color:var(--agent-muted); font-size:16px; }
</style>
