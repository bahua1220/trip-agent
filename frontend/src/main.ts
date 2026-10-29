import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import Home from './views/Home.vue'
import Result from './views/Result.vue'
import Auth from './views/Auth.vue'
import { getSession } from './services/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      path: '/result',
      name: 'Result',
      component: Result,
      meta: { requiresAuth: true }
    },
    {
      path: '/auth',
      name: 'Auth',
      component: Auth
    }
  ]
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !getSession()) {
    return {
      name: 'Auth',
      query: { redirect: to.fullPath }
    }
  }

  if (to.name === 'Auth' && getSession()) {
    const redirect = to.query.redirect
    return typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
      ? redirect
      : '/'
  }
})

const app = createApp(App)

app.use(router)
app.use(Antd)

app.mount('#app')

