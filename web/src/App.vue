<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const drawer = ref(false)
</script>

<template>
  <v-app>
    <button
      class="mobile-menu"
      type="button"
      aria-label="打开导航"
      @click="drawer = true"
    >
      <span
        class="mdi mdi-menu"
        aria-hidden="true"
      ></span>
    </button>
    <div
      v-if="drawer"
      class="nav-scrim"
      @click="drawer = false"
    ></div>
    <aside
      class="side-nav"
      :class="{ open: drawer }"
      aria-label="工作台导航"
    >
      <div class="nav-brand">
        Agent <span>工作台</span>
      </div>
      <button
        class="nav-close"
        type="button"
        aria-label="关闭导航"
        @click="drawer = false"
      >
        <span
          class="mdi mdi-close"
          aria-hidden="true"
        ></span>
      </button>
      <nav>
        <RouterLink
          to="/"
          :class="{ active: route.path === '/' }"
          @click="drawer = false"
        >
          <span
            class="mdi mdi-message-text-outline"
            aria-hidden="true"
          ></span>对话
        </RouterLink>
        <RouterLink
          to="/knowledge"
          :class="{ active: route.path.startsWith('/knowledge') }"
          @click="drawer = false"
        >
          <span
            class="mdi mdi-bookshelf"
            aria-hidden="true"
          ></span>知识库
        </RouterLink>
      </nav>
    </aside>
    <div class="app-content">
      <RouterView />
    </div>
  </v-app>
</template>

<style scoped>
.side-nav { width:216px; position:fixed; inset:0 auto 0 0; z-index:11; padding:23px 12px; background:#fff; border-right:1px solid #e6e6e9; }
.nav-brand { font-size:17px; font-weight:700; margin:2px 12px 34px; }
.nav-brand span { font-size:12px; color:var(--agent-muted); font-weight:400; margin-left:4px; }
nav { display:grid; gap:4px; }
nav a { display:flex; align-items:center; gap:13px; min-height:42px; padding:0 14px; color:#3b3b40; text-decoration:none; border-radius:7px; font-size:14px; }
nav a:hover, nav a.active { background:#f0f0f8; color:#3732a0; }
nav .mdi { font-size:20px; }
.app-content { min-width:0; margin-left:216px; }
.mobile-menu, .nav-close, .nav-scrim { display:none; }
@media (max-width:700px) {
  .side-nav { transform:translateX(-100%); transition:transform .18s ease; box-shadow:0 20px 40px #0002; }
  .side-nav.open { transform:translateX(0); }
  .app-content { margin-left:0; }
  .mobile-menu { display:grid; place-items:center; position:fixed; top:8px; left:8px; z-index:8; width:42px; height:42px; border:0; border-radius:7px; background:#fff; color:#34343a; font-size:22px; }
  .nav-close { display:block; position:absolute; top:20px; right:12px; border:0; background:none; font-size:21px; }
  .nav-scrim { display:block; position:fixed; inset:0; z-index:10; background:#0005; }
}
</style>
