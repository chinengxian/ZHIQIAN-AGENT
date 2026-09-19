import { config } from '@vue/test-utils'

config.global.stubs = {
  VApp: { template: '<div><slot /></div>' },
}
