import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'

import { createVuetify } from 'vuetify'

export default createVuetify({
  theme: {
    defaultTheme: 'agentLight',
    themes: {
      agentLight: {
        dark: false,
        colors: { background: '#f5f5f7', surface: '#ffffff', primary: '#635bff', error: '#b42318' },
      },
    },
  },
})
