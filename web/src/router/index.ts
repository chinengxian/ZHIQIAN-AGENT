import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import KnowledgeView from '../views/knowledge/KnowledgeView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: ChatView },
    { path: '/knowledge', component: KnowledgeView },
    { path: '/knowledge/:id', component: KnowledgeView },
  ],
})
