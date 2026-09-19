import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'coverage/**', 'node_modules/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { parser: tseslint.parser },
    },
    rules: {
      'vue/html-self-closing': ['error', {
        html: { void: 'always', normal: 'never', component: 'always' },
        svg: 'always',
        math: 'always',
      }],
      'vue/multi-word-component-names': 'off',
    },
  },
)
